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

# iris.init: skip the image's post-start script, which fails on this version
# (dbapi.connect); CallIn is enabled in iris.script instead.
# The diff makes sure IPM's own pip run changed no package.
RUN iris start IRIS \
 && iris session IRIS < iris.script \
 && iris stop IRIS quietly \
 && date > /usr/irissys/iris.init \
 && PYTHONPATH=/usr/irissys/mgr/python python3 -m pip check --disable-pip-version-check \
 && pip3 freeze --path /usr/irissys/mgr/python | sort > /tmp/installed.txt \
 && grep -v '^#' constraints.txt | sort | diff - /tmp/installed.txt \
 && rm /tmp/installed.txt
