FROM minio/mc as build
FROM codemowers/microservice-base
COPY --from=build /usr/bin/mc /usr/bin/mc
RUN pip3 install kopf httpx httpx_auth aiopg minio passlib miniopy-async aiomysql
ADD /app /app
WORKDIR /app
ENTRYPOINT /app/harbor-operator.py
