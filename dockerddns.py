#!/usr/bin/env python3
"""sample doc

"""
import argparse
import json
import logging
import socket
import sys


import dns
import dns.query
import dns.tsigkeyring
import dns.update
import docker

logging.basicConfig(
    format='%(asctime)s:%(levelname)s:%(message)s', level=logging.INFO)
logging.getLogger("requests").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("botocore").setLevel(logging.CRITICAL)

CONFIGFILE = 'dockerddns2.json'
TSIGFILE = 'secrets.json'
VERSION = 'rewrite1'


def str2bool(v):
    return v.lower() in ("yes", "true", "t", "1", "on")


def loadconfig():
    """
    Load the configuration file and return a dict
    """
    logging.debug('Loading Config Information')
    configfh = open(CONFIGFILE, mode='r')
    config = json.load(configfh)
    configfh.close()

    parser = argparse.ArgumentParser(
        description="Docker Dynamic DNS updater",
        epilog="All parameters must be configured on the config file, even if they are empty")
    parser.add_argument("--apiversion",
                        default=config['apiversion'],
                        help="Docker api version")
    parser.add_argument("--dnsserver", default=config['dnsserver'],
                        help="dns host to update")
    parser.add_argument("--dnsport", default=config['dnsport'],
                        help="DNS port to update")
    parser.add_argument("--ttl", default=config['ttl'],
                        help="Default TTL for the records")
    parser.add_argument("--keyname", default=config['keyname'],
                        help="Keyname from secrets.json")
    parser.add_argument("--zonename", default=config['zonename'],
                        help="Zone to update")
    parser.add_argument("--engine", default=config['engine'],
                        help="DNS Engine to use [bind|route53]")
    parser.add_argument("--hostedzone", default=config['hostedzone'],
                        help="Route53 Hostedzone ID")
    parser.add_argument("--intprefix", default=config['intprefix'],
                        metavar='ffc0::',
                        help="Internal IPv6 Prefix")
    parser.add_argument("--extprefix", default=config['extprefix'],
                        metavar='2001:db32::',
                        help="External IPv6 Prefix")
    parser.add_argument(
        "--ipv6replace",
        default=config['ipv6replace'],
        type=str2bool,
        metavar='true/false',
        help="replace intip with extip when updating the dns on IPv6")
    args = parser.parse_args()

    print(config)
    config = vars(args)
    logging.debug('Loading DNS Key Data')
    tsighandle = open(TSIGFILE, mode='r')
    config['keyring'] = dns.tsigkeyring.from_text(json.load(tsighandle))
    tsighandle.close()
    print("ARGS: %s" % config)

    return config


def startup(client):
    """
    This will do the initial check of already running containers and register
    them there is no cleanup if a container dies while this process is down,
    so you may have some leftovers after a while
    """
    logging.debug('Check running containers and update DDNS')
    for container in client.containers.list():
        containerinfo = container_info(json.dumps(container.attrs))
        if containerinfo:
            updatedns('start', containerinfo)


def container_info(container):
    """
    Process the container.attrs from docker client and return our own docker
    dict
    this will return blank when the container is run on net=host as no info
    is provided by docker on ip address
    """
    inspect = json.loads(container)
    container = {}
    container['fulljson'] = inspect
    networkmode = inspect["HostConfig"]["NetworkMode"]
    container['hostname'] = inspect["Config"]["Hostname"]
    container['id'] = inspect["Id"]
    container['name'] = inspect["Name"].split('/', 1)[1]
    if "services" in inspect["Config"]["Labels"]:
        container['srvrecords'] = inspect["Config"]["Labels"]["services"]
        print("%s\n" % (container['srvrecords']))
    if (str(networkmode) != 'host') and ('container:' not in networkmode):
        if str(networkmode) == "default":
            networkmode = "bridge"
        container['ip'] = \
            inspect["NetworkSettings"]["Networks"][networkmode]["IPAddress"]
        container['ipv6'] = \
            inspect["NetworkSettings"]["Networks"][networkmode]["GlobalIPv6Address"]
    else:
        return False
    return container


def updatedns(action, event):
    """
    This function will prepare the information from docker before send
    it to the dns engine
    """

    config = loadconfig()
    if "ipv6" in event:
        if event['ipv6'] != "" and config['ipv6replace'] is True:
            ipv6addr = event['ipv6'].replace(config['intprefix'],
                                             config['extprefix'])
            event['ipv6'] = ipv6addr
    if config['engine'] == "bind":
        return dockerbind(action, event, config)
    elif config['engine'] == "route53":
        return docker53(action, event, config)
    return False


def docker53(action, event, config):
    """
    This function will update a hosted zone registry in AWS route53
    """
    import boto3
    client = boto3.client('route53')
    changes = []

    try:
        hostedzone = client.get_hosted_zone(Id=config['hostedzone'])
        event['hostname'] = event['hostname'] + \
            "." + hostedzone['HostedZone']['Name']
    except Exception as exception:
        logging.exception('%s', exception)
        return

    if action == "start":
        action = "UPSERT"
        change = {'Action': action,
                  'ResourceRecordSet': {'Name': event['hostname'],
                                        'Type': 'A',
                                        'TTL': 300,
                                        'ResourceRecords': [{'Value': event['ip']}]}}
        changes.append(change)
        if "ipv6" in event:
            change = {'Action': action, 'ResourceRecordSet':
                      {'Name': event['hostname'], 'Type': 'AAAA',
                       'TTL': 300, 'ResourceRecords': [
                          {'Value': event['ipv6']}]}}
        changes.append(change)
        if "ipv6" in event:
            change = {'Action': 'UPSERT', 'ResourceRecordSet':
                      {'Name': event['hostname'], 'Type': 'AAAA',
                       'TTL': 300, 'ResourceRecords': [
                          {'Value': event['ipv6']}]}}
            changes.append(change)
        else:
            event['ipv6'] = "None"
            changes.append(change)
        if event['ipv6']:
            logging.info('[%s] Updating route53, setting %s to ipv6 %s',
                         event['name'], event['hostname'],
                         event['ipv6'])
        logging.info('[%s] Updating route53, setting %s to ipv4 %s',
                     event['name'], event['hostname'],
                     event['ip'])

    elif action == "die":
        action = "DELETE"
        #
        # Check for IPv4 Records
        #
        response = client.list_resource_record_sets(
            HostedZoneId=config['hostedzone'],
            StartRecordName=event['hostname'],
            StartRecordType='A',
            MaxItems='1'
        )
#        #"""
#        # If the number of ResourceRecordSets is 0, means no current entry exists
#        #"""

        if not response['ResourceRecordSets']:
            logging.info('RESPONSE FALSE')
            return False

#        #"""
#        # Check for IPv6 Records
#        #"""

        responsev6 = client.list_resource_record_sets(
            HostedZoneId=config['hostedzone'],
            StartRecordName=event['hostname'],
            StartRecordType='AAAA',
            MaxItems='1'
        )
#        #"""
#        # If the number of ResourceRecordSets is 0, means no current entry exists
#        #"""
        if responsev6['ResourceRecordSets'] \
                and responsev6['ResourceRecordSets'][0]['Name'] == event['hostname']:
            change = {
                'Action': action,
                'ResourceRecordSet': {
                    'Name': event['hostname'],
                    'Type': 'AAAA',
                    'TTL': 300,
                    'ResourceRecords': [
                        {
                            'Value': responsev6['ResourceRecordSets'][0]['ResourceRecords'][0]['Value']}]}}
            changes.append(change)
            logging.info(
                '[%s] Removing %s from route53 with ipv6 %s',
                event['name'],
                event['hostname'],
                responsev6['ResourceRecordSets'][0]['ResourceRecords'][0]['Value'])

        if response['ResourceRecordSets'] and \
                response['ResourceRecordSets'][0]['Name'] == event['hostname']:
            change = {
                'Action': action,
                'ResourceRecordSet': {
                    'Name': event['hostname'],
                    'Type': 'A',
                    'TTL': 300,
                    'ResourceRecords': [
                        {
                            'Value': response['ResourceRecordSets'][0]['ResourceRecords'][0]['Value']}]}}
            changes.append(change)
            logging.info(
                '[%s] Removing %s from route53 with ip %s',
                event['name'],
                event['hostname'],
                response['ResourceRecordSets'][0]['ResourceRecords'][0]['Value'])

    change = {'Action': action,
              'ResourceRecordSet': {'Name': event['hostname'],
                                    'Type': 'TXT',
                                    'TTL': 300,
                                    'ResourceRecords': [{'Value': event['id']}]}}
    changes.append(change)
    response = client.change_resource_record_sets(
        HostedZoneId=config['hostedzone'],
        ChangeBatch={
            'Changes': changes
        })


def dockerbind(action, event, config):
    """
    This will update a zone in a bind dns configured for dynamic updates
    """
    dnsserver = config['dnsserver']
    ttl = config['ttl']
    port = config['dnsport']
    update = dns.update.Update(
        config['zonename'],
        keyring=config['keyring'],
        keyname=config['keyname'])
    logging.debug('EVENT: %s', event)
    if "srvrecords" in event:
        srvrecords = event["srvrecords"].split()
        for srv in srvrecords:
            values = srv.split("#")
            print("%s %s\n" % (values, event['hostname']))

#    update.replace(event['hostname'], ttl, 'TXT', "ContainerId:" + event['id'] + ",DockerHost:" + event['host'])
    if action == 'start' and event['ip'] != '0.0.0.0':
        update.replace(event['hostname'], ttl, 'A', event['ip'])
        if event['ipv6'] != '':
            update.replace(event['hostname'], ttl, 'AAAA', event['ipv6'])
            logging.info(
                '[%s] Updating dns %s , setting %s.%s to %s and %s',
                event['name'],
                dnsserver,
                event['hostname'],
                config['zonename'],
                event['ip'],
                event['ipv6'])
        else:
            logging.info('[%s] Updating dns %s , setting %s.%s to %s',
                         event['name'], dnsserver, event['hostname'],
                         config['zonename'], event['ip'])

    elif action == 'die':
        logging.info('[%s] Removing entry for %s.%s in %s',
                     event['name'], event['hostname'], config['zonename'],
                     dnsserver)
        update.delete(event['hostname'])

    try:
        response = dns.query.tcp(update, dnsserver, timeout=10, port=port)
    except (socket.error, dns.exception.Timeout):
        logging.error('Timeout updating DNS')
        response = 'Timeout Socket'
    except dns.query.UnexpectedSource:
        logging.error('Unexpected Source')
        response = 'UnexpectedSource'
    except dns.tsig.PeerBadKey:
        logging.error('Bad Key for DNS, Check your config files')
        response = "BadKey"

    if response.rcode() != 0:
        logging.error("[%s] Error Reported while updating %s (%s/%s)",
                      event['name'], event['hostname'],
                      dns.rcode.to_text(response.rcode()), response.rcode())


def process():
    """
    This is the main function that will be called everytime this run
    """
    config = loadconfig()
    logging.info('Starting Docker DDNS Python Container %s', VERSION)
    logging.info('Using %s as dns engine', config['engine'])
    logging.info('Docker Python SDK Version: %s', docker.constants.version)
    logging.info('Lower Docker API: %s',
                 docker.constants.MINIMUM_DOCKER_API_VERSION)
    if "apiversion" in config:
        if config['apiversion'] == "auto":
            config['apiversion'] = docker.constants.DEFAULT_DOCKER_API_VERSION
        print(config['apiversion'])
        if float(config['apiversion']) < float(
                docker.constants.MINIMUM_DOCKER_API_VERSION):
            logging.error(
                'Can\'t use API Version lower than supported by docker python SDK')
            logging.error('Requested Version: %s', config['apiversion'])
            logging.error('Minimum Supported Docker API Version: %s',
                          docker.constants.MINIMUM_DOCKER_API_VERSION)
            sys.exit(3)
    else:
        config['apiversion'] = docker.constants.DEFAULT_DOCKER_API_VERSION
    logging.info('Requested Docker API Version: %s', config['apiversion'])
    client = docker.from_env(version=config['apiversion'])
    events = client.events(decode=True)
    startup(client)
    for event in events:
        if event['Type'] == "container" and event['Action'] in (
                'start', 'die'):
            temp = client.containers.get(event['id'])
            containerinfo = container_info(json.dumps(temp.attrs))
            if event['Action'] == 'start':
                if containerinfo:
                    logging.debug(
                        "Container %s is starting with hostname %s and ipAddr %s",
                        containerinfo['name'],
                        containerinfo['hostname'],
                        containerinfo['ip'])
                    updatedns(event['Action'], containerinfo)
            elif event['Action'] == 'die':
                if containerinfo:
                    logging.debug("Container %s is stopping %s",
                                  containerinfo['name'],
                                  containerinfo['hostname'])
                    updatedns(event['Action'], containerinfo)


def main():
    """
    Main
    """
    try:
        process()
    except KeyboardInterrupt:
        logging.info('CTRL-C Pressed, GoodBye!')
        sys.exit()


if __name__ == "__main__":
    main()
