# intersystemsdc/iris-community, IRIS 2026.1.0.234.1com (Python 3.12.3)
ARG IMAGE=intersystemsdc/iris-community@sha256:228654b169301774a7afa5450d4238fd3f3bae1a5be6a6ca3336e38144cdbdd1
FROM $IMAGE

WORKDIR /home/irisowner/dev
COPY --chown=irisowner:irisowner . .

# Python libraries for embedded Python
RUN pip3 install --no-cache-dir --disable-pip-version-check --target /usr/irissys/mgr/python \
        -r requirements.txt -c constraints.txt \
 && PYTHONPATH=/usr/irissys/mgr/python python3 -m pip check --disable-pip-version-check

ENV HF_HOME=/home/irisowner/.cache/huggingface

# Install the package into IRIS.
# iris.init: skip the image's post-start script, which fails on this version
# (dbapi.connect); CallIn is enabled in iris.script instead.
RUN iris start IRIS \
 && tr -d '\r' < iris.script | iris session IRIS \
 && iris stop IRIS quietly \
 && date > /usr/irissys/iris.init

# The Python libraries still fit together after the IPM install
RUN PYTHONPATH=/usr/irissys/mgr/python python3 -m pip check --disable-pip-version-check

# IPM changed no Python package: the installed versions are still exactly those in
# constraints.txt (Windows line endings in the file are ignored)
RUN tr -d '\r' < constraints.txt | grep -v -e '^#' -e '^$' | sort > /tmp/expected.txt \
 && pip3 freeze --path /usr/irissys/mgr/python | sort > /tmp/installed.txt \
 && if ! diff /tmp/expected.txt /tmp/installed.txt; then \
      echo "The installed Python packages differ from constraints.txt (< expected, > installed)."; \
      exit 1; \
    fi \
 && rm /tmp/expected.txt /tmp/installed.txt
