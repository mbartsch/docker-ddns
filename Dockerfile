FROM alpine:latest AS builder
MAINTAINER Marcelo Bartsch <marcelo@bartsch.cl>

COPY requirements.txt /
RUN apk --no-cache add python3 py-pip python3-dev libc-dev gcc docker 
RUN pip3 install --user -r /requirements.txt 

FROM alpine:latest
COPY dockerddns.py secrets.json dockerddns.json /ddns/
RUN apk --no-cache add python3
RUN chmod +x /ddns/dockerddns.py
COPY --from=builder /root/.local /root/.local
WORKDIR /ddns
ENTRYPOINT [ "/ddns/dockerddns.py" ]
