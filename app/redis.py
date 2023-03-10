#!/usr/bin/env python3
import asyncio
import kopf
import logging
import os
from base64 import b64decode
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio import client, config, utils
from lib import Secret, make_selector, parse_capacity

REDIS_PORT = 6379


@kopf.on.delete("redises.codemowers.io")
async def deletion(name, namespace, body, **kwargs):
    api_client = client.ApiClient()
    apps_api = client.AppsV1Api()
    api_instance = client.CustomObjectsApi(api_client)
    v1 = client.CoreV1Api(api_client)
    class_body = await api_instance.get_cluster_custom_object(
        "codemowers.io",
        "v1alpha1",
        "clusterredisclasses",
        body["spec"]["class"])
    target_namespace = class_body["spec"].get("targetNamespace", namespace)
    instance = name
    if "targetNamespace" in class_body["spec"]:
        instance = "%s-%s" % (namespace, instance)
    service_name = "redis-cluster-%s" % instance
    headless_name = "%s-headless" % service_name
    await v1.delete_namespaced_service(service_name, target_namespace)
    await v1.delete_namespaced_service(headless_name, target_namespace)
    await apps_api.delete_namespaced_stateful_set(
        "redis-cluster-%s" % instance,
        target_namespace)
    await v1.delete_namespaced_secret(
        "redis-cluster-%s-secrets" % instance,
        target_namespace)


@kopf.on.resume("redises.codemowers.io")
@kopf.on.create("redises.codemowers.io")
async def creation(name, namespace, body, **kwargs):
    print("Handling", namespace, name)
    api_client = client.ApiClient()
    api_instance = client.CustomObjectsApi(api_client)
    v1 = client.CoreV1Api(api_client)

    class_body = await api_instance.get_cluster_custom_object(
        "codemowers.io",
        "v1alpha1",
        "clusterredisclasses",
        body["spec"]["class"])

    # Handle target namespace/cluster mapping
    target_namespace = class_body["spec"].get("targetNamespace", namespace)
    instance = name

    # TODO: Make sure origin namespace/name do not contain dashes,
    # or find some other trick to prevent name collisions

    # Prefix instance name with origin namespace if
    # we're hoarding instances into single namespace
    if "targetNamespace" in class_body["spec"]:
        instance = "%s-%s" % (namespace, instance)

    # Service hostname and FQDN
    service_name = "redis-cluster-%s" % instance
    service_fqdn = "%s.%s.svc.cluster.local" % (service_name, target_namespace)
    headless_name = "%s-headless" % service_name

    # Derive owner object for Kopf
    owner = body if target_namespace == namespace else class_body

    capacity = body["spec"]["capacity"]
    replicas = class_body["spec"]["replicas"]

    labels, label_selector = make_selector("redis", instance)

    pod_spec = class_body["spec"].get("podSpec", {})
    storage_class = class_body["spec"].get("storageClass", None)

    sec = Secret(target_namespace, "redis-cluster-%s-secrets" % instance)

    if pod_spec:
        # AZ handling
        pod_spec["affinity"] = {
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [{
                    "labelSelector": label_selector,
                    "topologyKey": class_body["spec"].get("topologyKey", "topology.kubernetes.io/zone")
                }]
            }
        }

        pod_spec["volumes"] = [{
            "name": "config",
            "secret": {
                "secretName": sec.name
            }
        }]

        # Create cluster secrets
        body = sec.wrap([{
            "key": "REDIS_PASSWORD",
            "value": "%(plaintext)s"
        }, {
            "key": "redis.conf",
            "value": "masterauth \"%(plaintext)s\"\nrequirepass \"%(plaintext)s\"\n",
        }])

        kopf.append_owner_reference(body, owner, block_owner_deletion=False)
        try:
            await v1.create_namespaced_secret(target_namespace, client.V1Secret(**body))
        except ApiException as e:
            if e.status == 409:
                logging.info("Secret %s/%s already generated" % (target_namespace, sec.name))
            else:
                raise
        else:
            logging.info("Created secret %s/%s" % (target_namespace, sec.name))

        # Assume it's the first container in the pod
        container_spec = pod_spec["containers"][0]

        args = [
            "--maxmemory",
            "%d" % parse_capacity(capacity),
        ]

        if "keydb" in container_spec["image"].lower():
            if replicas > 1:
                args += [
                    "--active-replica",
                    "yes",
                    "--multi-master",
                    "yes"
                ]
            # KeyDB does not support multiple "databases"
            extra_secret_mappings = []
        elif "redis" in container_spec["image"].lower():
            if replicas > 1:
                raise NotImplementedError("Multiple replica deployment of vanilla Redis not supported")
        else:
            raise NotImplementedError("Don't know which implementation to use for image %s" % repr(container_spec["image"]))

        if not storage_class:
            args += [
                "--save",
                ""
            ]

        # Create stateful set
        container_spec["args"] = container_spec.get("args", []) + args
        container_spec["env"] = [{
            "name": "SERVICE_NAME",
            "value": headless_name,
        }, {
            "name": "REPLICAS",
            "value": " ".join([("redis-cluster-%s-%d" % (instance, j)) for j in range(0, replicas)])
        }]
        container_spec["volumeMounts"] = [{
            "name": "config",
            "mountPath": "/etc/redis",
            "readOnly": True
        }]

        statefulset_body = {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {
                "namespace": target_namespace,
                "name": "redis-cluster-%s" % instance,
                "labels": labels,
            },
            "spec": {
                "selector": {
                    "matchLabels": labels,
                },
                "serviceName": headless_name,
                "replicas": replicas,
                "podManagementPolicy": "Parallel",
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "redises.codemowers.io/class": class_body["metadata"]["name"]
                        }
                    },
                    "spec": pod_spec,
                },
            }
        }

        if storage_class:
            statefulset_body["spec"]["volumeClaimTemplates"] = [{
                "metadata": {
                    "name": "data",
                },
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {
                        "requests": {
                            # Double the capacity to accommodate BGSAVE and
                            # represent in mebibytes
                            "storage": "%dMi" % (parse_capacity(capacity) // 524288),
                        }
                    },
                    "storageClassName": class_body["spec"]["storageClass"],
                }
            }]

        kopf.append_owner_reference(statefulset_body, owner, block_owner_deletion=False)
        try:
            await utils.create_from_yaml_single_item(api_client, statefulset_body)
        except ApiException as e:
            if e.status == 409:
                logging.info("Secret %(namespace)s/%(name)s already generated" % statefulset_body["metadata"])
            else:
                raise

        # Create service
        body = {
            "kind": "Service",
            "apiVersion": "v1",
            "metadata": {
                "namespace": target_namespace,
                "name": service_name,
            },
            "spec": {
                "selector": labels,
                "sessionAffinity": "ClientIP",
                "type": "ClusterIP",
                "ports": [{
                    "port": REDIS_PORT,
                    "name": "redis",
                }]
            }

        }
        kopf.append_owner_reference(body, owner, block_owner_deletion=False)
        try:
            await utils.create_from_yaml_single_item(api_client, body)
        except ApiException as e:
            if e.status == 409:
                logging.info("Service %(namespace)s/%(name)s already generated" % body["metadata"])
            else:
                raise

        # Create headless service
        body = {
            "kind": "Service",
            "apiVersion": "v1",
            "metadata": {
                "namespace": target_namespace,
                "name": headless_name,
            },
            "spec": {
                "selector": labels,
                "clusterIP": "None",
                "publishNotReadyAddresses": True,
                "ports": [{
                    "name": "redis",
                    "port": REDIS_PORT
                }]
            }
        }

        kopf.append_owner_reference(body, owner, block_owner_deletion=False)
        try:
            await utils.create_from_yaml_single_item(api_client, body)
        except ApiException as e:
            if e.status == 409:
                logging.info("Headless service %(namespace)s/%(name)s already generated" % body["metadata"])
            else:
                raise

        # Create database secrets
        cluster_secrets = await v1.read_namespaced_secret(sec.name, target_namespace)
        database_secrets = Secret(
            namespace,
            "redis-%s-owner-secrets" % name,
            b64decode(cluster_secrets.data["REDIS_PASSWORD"]).decode("ascii")
        )
        body = database_secrets.wrap([{
            "key": "REDIS_PASSWORD",
            "value": "%(plaintext)s"
        }, {
            "key": "REDIS_HOST_PORT",
            "value": "%s:%d" % (service_fqdn, REDIS_PORT),
        }, {
            "key": "REDIS_HOST",
            "value": service_fqdn,
        }, {
            "key": "REDIS_PORT",
            "value": str(REDIS_PORT),
        }, {
            "key": "REDIS_URI",
            "value": "redis://:%%(plaintext)s@%s" % service_fqdn,
        }] + [{
            "key": "REDIS_%d_URI" % j,
            "value": "redis://:%%(plaintext)s@%s/%d" % (service_fqdn, j),
        } for j in range(0, 16)])
        kopf.append_owner_reference(body, block_owner_deletion=False)
        try:
            await v1.create_namespaced_secret(namespace, client.V1Secret(**body))
        except ApiException as e:
            if e.status == 409:
                logging.info("Secret %s/%s already generated" % (namespace, database_secrets.name))
            else:
                raise
        else:
            logging.info("Created secret %s/%s" % (namespace, database_secrets.name))
    return {"state": "READY"}


@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    if os.getenv("KUBECONFIG"):
        await config.load_kube_config()
    else:
        config.load_incluster_config()

    settings.scanning.disabled = True
    settings.posting.enabled = True
    settings.persistence.finalizer = "redis-operator"
    logging.info("redis-operator starting up")

asyncio.run(kopf.operator(clusterwide=True))
