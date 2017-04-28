# THIS IS AN EXPERIMENTAL BRANCH
In this branch I'm adding support for AWS Route53 Hosted Zones and also
a rewrite of the dockerddns file. 
Other important change is that the files has been renamed from 
docker-ddns to dockerddns

# Hot To Run
docker run -it --rm \
	-v /var/run/docker.sock:/var/run/docker.sock \
	-v /myconfiglocation/secrets.json:/ddns/secrets.json \
	-v /myconfiglocation/dockerddns.json:/ddns/dockerddns.json \
	 mbartsch/ddns:0

# Config Needed

## dockerddns.json
```
{
  "dockerddns": {
    "apiversion" : "auto",
    "dnsserver"  : "my.dns.server",
    "dnsport"    : 53,
    "keyname"    : "my.dns.key",
    "zonename"   : "dynamic.mydomain.ntld",
    "intprefix"  : "",
    "extprefix"  : "",
    "ttl"        : 60,
    "engine"     : "bind",
    "hostedzone" : "ROUTE53HOSTEDZONEID"
  }
}

dnsserver  = hostname of bind
dnsport    = port used by bind , you can change it if 53 is blocked
keyname    = the keyname
zonename   = ddns zone
intprefix  = IPv6 prefix on the internal network
extprefix  = IPv6 on the external network
apiversion = specify the api version to use when talking to the docker server, auto by default
engine     = dns engine, currently bind and aws route53
hostedzone = Route53 Hosted Zone Id
```
for how to use intprefix and extprefix please check this gists:
https://gist.github.com/mbartsch/5f0b0ab414d3e901f38388792a88321c


## secrets.json


{"my.key.file":"base64_encrypted_key"}

left side  = key name as in named.conf
right side = mykeyfilesecret in base64 , same as in named.conf

## bind setup
in your named.conf you must have:

```
key "my.key.file" {
  algorithm hmac-md5;
  secret "mykeyfilesecret";
};

zone "myddnszone.mydomain.xtld" IN {
        type master;
        file "dynamic/myddnszone.mydomain.xtld.zone";
        allow-update { key "my.key.file"; };
};
```


This guide explain in details the needed steps:

https://www.kirya.net/articles/running-a-secure-ddns-service-with-bind/

# TODO
This is the list of features I'm planning to implement at some point, in no particular order
   * SRV Records
   * Cleanup Stale Records
