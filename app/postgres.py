#!/usr/bin/env python3
import aiopg
import asyncio
import kopf
import logging
import os
import psycopg2
from base64 import b64decode
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio import client, config
from lib import Secret, make_selector


@kopf.on.resume("postgresdatabases.codemowers.io")
@kopf.on.create("postgresdatabases.codemowers.io")
async def creation(name, namespace, body, **kwargs):
    api_client = client.ApiClient()
    api_instance = client.CustomObjectsApi(api_client)
    v1 = client.CoreV1Api(api_client)

    class_body = await api_instance.get_cluster_custom_object(
        "codemowers.io",
        "v1alpha1",
        "clusterpostgresdatabaseclasses",
        body["spec"]["class"])

    # Handle target namespace/cluster mapping
    target_namespace = class_body["spec"].get("targetNamespace", namespace)
    instance = class_body["spec"].get("targetCluster", name)

    # TODO: Make sure origin namespace/name do not contain dashes,
    # or find some other trick to prevent name collisions

    # Prefix instance name with origin namespace if
    # we're hoarding instances into single namespace
    if "targetNamespace" in class_body["spec"] and "targetCluster" not in class_body["spec"]:
        instance = "%s-%s" % (namespace, instance)

    # Derive owner object for Kopf
    owner = body if target_namespace == namespace else class_body

    capacity = body["spec"]["capacity"]
    replicas = class_spec["replicas"]
    routers = class_spec["routers"]

    labels, label_selector = make_selector("postgres", instance)

    pod_spec = class_body["spec"].get("podSpec", {})
    storage_class = class_body["spec"].get("storageClass", None)

    if storage_class:
        body = {
            "apiVersion": "postgres-operator.crunchydata.com/v1beta1",
            "kind": "PostgresCluster",
            "metadata": {
                "namespace": target_namespace,
                "name": "postgres-%s" % instance,
                "labels": labels,
            },
            "spec": {
                "proxy": {
                  "pgBouncer": {
                    "replicas": routers
                  }
                },
                "users": [{
                    "name": "postgres",
                }],
                "postgresVersion": 14,
                "instances": [{
                    **pod_spec,
                    "name": "cluster",
                    "replicas": replicas,
                    "affinity": {
                        "podAntiAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": [{
                                "labelSelector": label_selector,
                                "topologyKey": class_body["spec"].get("topologyKey", "topology.kubernetes.io/zone")
                            }]
                        }
                    },
                    "dataVolumeClaimSpec": {
                        "storageClassName": storage_class,
                        "accessModes": ["ReadWriteOnce"],
                        "resources": {
                            "requests": {
                                "storage": capacity,
                            }
                        }
                    }
                }],
                "backups": {
                    "pgbackrest": {
                        "repos": [{
                            "name": "repo1",
                            "volume": {
                                "volumeClaimSpec": {
                                    "accessModes": ["ReadWriteOnce"],
                                    "resources": {
                                        "requests": {
                                            "storage": 2 * capacity
                                        }
                                    }
                                }
                            }
                        }]
                    }
                }
            }
        }

        kopf.append_owner_reference(body, owner, block_owner_deletion=False)
        try:
            await api_instance.create_namespaced_custom_object(
                "postgres-operator.crunchydata.com",
                "v1beta1",
                target_namespace,
                "postgresclusters",
                body)
        except ApiException as e:
            if e.status == 409:
                logging.info("Postgres cluster %s/%s already generated" % (
                    target_namespace, "postgres-%s" % instance))
            else:
                raise
        else:
            logging.info("Created Postgres cluster %s/%s" % (
                target_namespace, "postgres-%s" % instance))

    # Fetch secrets to create bucket
    cluster_secrets = await v1.read_namespaced_secret(
        "postgres-%s-pguser-postgres" % instance,
        target_namespace)
    cluster_port = int(b64decode(cluster_secrets.data["port"]).decode("ascii"))
    cluster_hostname = b64decode(cluster_secrets.data["pgbouncer-host"]).decode("ascii")

    conn = await aiopg.connect(
        database="postgres",
        user=b64decode(cluster_secrets.data["user"]).decode("ascii"),
        password=b64decode(cluster_secrets.data["password"]).decode("ascii"),
        port=cluster_port,
        host=cluster_hostname)

    # Create database
    user_name = database_name = ("%s_%s" % (namespace, name)).replace("-", "_")

    cursor = await conn.cursor()

    try:
        # TODO: why binding doesnt work here?!
        await cursor.execute("CREATE DATABASE \"%s\"" % database_name)
    except psycopg2.errors.DuplicateDatabase:
        pass

    # Create secret for accessing bucket
    database_secrets = Secret(namespace, "postgres-database-%s-owner-secrets" % name)
    body = database_secrets.wrap([{
        "key": "PGHOST",
        "value": cluster_hostname,
    }, {
        "key": "PGUSER",
        "value": user_name
    }, {
        "key": "PGPORT",
        "value": "5432"
    }, {
        "key": "PGPASSWORD",
        "value": "%(plaintext)s"
    }, {
        "key": "PGDATABASE",
        "value": database_name
    }, {
        "key": "DATABASE_URL",
        "value": "postgres://%s:%%(plaintext)s@%s:%d/%s" % (
            user_name, cluster_hostname, cluster_port, database_name)
    }])

    await cursor.execute("CREATE USER %s WITH ENCRYPTED PASSWORD %s;" % (
        user_name, repr(database_secrets["plaintext"])))

    kopf.append_owner_reference(body, owner, block_owner_deletion=False)
    try:
        await v1.create_namespaced_secret(namespace, client.V1Secret(**body))
    except ApiException as e:
        if e.status == 409:
            logging.info("Secret %s/%s already generated" % (namespace, database_secrets.name))
        else:
            raise
    else:
        logging.info("Created secret %s/%s" % (namespace, database_secrets.name))

    await cursor.execute("GRANT ALL PRIVILEGES ON DATABASE \"%s\" TO \"%s\"" % (
        database_name, user_name))

    return {"state": "READY"}


@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    if os.getenv("KUBECONFIG"):
        await config.load_kube_config()
    else:
        config.load_incluster_config()

    settings.scanning.disabled = True
    settings.posting.enabled = True
    settings.persistence.finalizer = "postgres-operator"
    logging.info("postgres-operator starting up")

asyncio.run(kopf.operator(clusterwide=True))
