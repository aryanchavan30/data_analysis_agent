FROM ghcr.io/python-discord/snekbox:latest

# Install data-analysis libraries into the user site-packages
# so they are available inside the NsJail sandbox.
USER root

RUN /snekbox/venv/bin/pip install --no-cache-dir \
    pandas \
    numpy \
    openpyxl \
    matplotlib

USER snekbox
