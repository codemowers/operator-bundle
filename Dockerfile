FROM minio/mc as build
FROM harbor.k-space.ee/k-space/microservice-base
COPY --from=build /usr/bin/mc /usr/bin/mc
RUN pip3 install kopf httpx httpx_auth aiopg minio passlib miniopy-async
ADD /app /app
WORKDIR /app
ENTRYPOINT /app/harbor-operator.py
