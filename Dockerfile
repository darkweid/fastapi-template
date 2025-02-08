FROM ubuntu:latest
LABEL authors="mark"

ENTRYPOINT ["top", "-b"]