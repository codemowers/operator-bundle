#!/usr/bin/env python3
import asyncio
import kopf
import logging
import os
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio import client, config
from lib import Secret


@kopf.on.resume("secrets.codemowers.io")
@kopf.on.create("secrets.codemowers.io")
async def creation(name, namespace, body, **kwargs):
    api_client = client.ApiClient()
    v1 = client.CoreV1Api(api_client)

    # Construct secret for cluster secrets
    sec = Secret(namespace, name)
    body = sec.wrap(body["spec"]["mapping"])
    kopf.append_owner_reference(body)
    try:
        await v1.create_namespaced_secret(namespace, client.V1Secret(**body))
    except ApiException as e:
        if e.status == 409:
            logging.info("Secret %s/%s already generated" % (namespace, sec.name))
        else:
            raise
    else:
        logging.info("Created secret %s/%s" % (namespace, sec.name))
    return {"state": "READY"}


@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    if os.getenv("KUBECONFIG"):
        await config.load_kube_config()
    else:
        config.load_incluster_config()
    settings.scanning.disabled = True
    settings.posting.enabled = True
    settings.persistence.finalizer = "secret-operator"
    logging.info("secret-operator starting up")


asyncio.run(kopf.operator(clusterwide=True))
