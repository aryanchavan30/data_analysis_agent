FROM ghcr.io/python-discord/snekbox:latest

USER root

RUN /usr/local/bin/pip install --no-cache-dir \
    pandas \
    numpy \
    openpyxl \
    matplotlib

# Append /data bind mount so NsJail sandbox can read uploaded files
RUN echo '\nmount {\n    src: "/data"\n    dst: "/data"\n    is_bind: true\n    rw: false\n}' \
    >> /snekbox/config/snekbox.cfg