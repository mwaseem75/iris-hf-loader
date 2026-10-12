ARG IMAGE=intersystemsdc/iris-community:latest
FROM $IMAGE

WORKDIR /home/irisowner/dev
COPY --chown=irisowner:irisowner . .

# Python libraries for embedded Python (datasets, pandas, pyarrow)
RUN pip3 install --target /usr/irissys/mgr/python -r requirements.txt

ENV HF_HOME=/home/irisowner/.cache/huggingface

RUN iris start IRIS && \
    iris session IRIS < iris.script && \
    iris stop IRIS quietly
