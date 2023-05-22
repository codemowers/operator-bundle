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
from lib2 import ShareableMixin, PersistentMixin, CustomResourceMixin, RoutedMixin, CapacityMixin, ClaimMixin, ClassedOperator

class PostgresDatabase(ShareableMixin, PersistentMixin, CustomResourceMixin, RoutedMixin, CapacityMixin, ClaimMixin, ClassedOperator):
    """
    Postgres database operator implementation using CloudNativePG
    """
    OPERATOR = "codemowers.io/postres-database-operator"
    GROUP = "codemowers.io"
    VERSION = "v1alpha1"
    SINGULAR = "PostgresDatabase"
    PLURAL = "PostgresDatabases"

    def generate_custom_resource(self):
        return {
            "apiVersion": "postgresql.cnpg.io/v1",
            "kind": "Cluster",
            "metadata": {
                "namespace": self.get_target_namespace(),
                "name": self.get_target_name(),
            },
            "spec": {
                "instances": self.class_spec["replicas"],
                "storage": {
                    "size": self.get_capacity(),
                    "storageClassName": self.class_spec.get("storageClass"),
                },
                "monitoring": {
                    "enablePodMonitor": "true"
                }
            }
        }

if __name__ == "__main__":
    PostgresDatabase.run()


"""
@kopf.on.resume("postgresdatabases.codemowers.io")
@kopf.on.create("postgresdatabases.codemowers.io")
async def creation(name, namespace, body, **kwargs):
    target_namespace, instance, owner, api_client, api_instance, class_spec = await resolve_instance(
        namespace, name, body)
    v1 = client.CoreV1Api(api_client)

    capacity = body["spec"]["capacity"]
    replicas = class_spec["replicas"]
    routers = class_spec["routers"]

    labels, label_selector = make_selector("postgres", instance)

    pod_spec = class_spec.get("podSpec", {})
    storage_class = class_spec.get("storageClass", None)

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
                                "topologyKey": class_spec.get("topologyKey", "topology.kubernetes.io/zone")
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
                                            "storage": "%dMi" % (parse_capacity(capacity) // 524288),
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
    cluster_hostname = b64decode(cluster_secrets.data["host"]).decode("ascii")

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

#asyncio.run(kopf.operator(clusterwide=True))
"""
