#!/usr/bin/env python3
import aiomysql
import asyncio
import kopf
import logging
import os
from base64 import b64decode
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio import client, config
from lib2 import ShareableMixin, PersistentMixin, CustomResourceMixin, RoutedMixin, CapacityMixin, ClassedOperator

class MysqlDatabase(ShareableMixin, PersistentMixin, CustomResourceMixin, RoutedMixin, CapacityMixin, ClassedOperator):
    """
    MySQL operator implementation
    """
    GROUP = "codemowers.io"
    VERSION = "v1alpha1"
    SINGULAR = "MysqlDatabase"
    PLURAL = "MysqlDatabases"

    def generate_custom_resource(self):
        return {
            "apiVersion": "mysql.oracle.com/v2",
            "kind": "InnoDBCluster",
            "metadata": {
                "namespace": self.get_target_namespace(),
                "name": self.get_target_name(),
            },
            "spec": {
                "tlsUseSelfSigned": True,
                #"secretName": sec.name,
                "instances": self.class_spec["replicas"],
                "router": {
                    "instances": self.class_spec["routers"],
                    "podSpec": {
                        "affinity": {
                            "podAntiAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": [{
                                    "labelSelector": self.label_selector,
                                    "topologyKey": self.class_spec.get("topologyKey", "topology.kubernetes.io/zone")
                                }]
                            }
                        },
                    }
                },
                "datadirVolumeClaimTemplate": {
                    "storageClassName": self.class_spec.get("storageClass"),
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {
                        "requests": {
                            "storage": self.get_capacity(),
                        }
                    }
                },
                "podSpec": {
                    **self.class_spec.get("podSpec", {}),
                    "affinity": {
                        "podAntiAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": [{
                                "labelSelector": self.label_selector,
                                "topologyKey": self.class_spec.get("topologyKey", "topology.kubernetes.io/zone")
                            }]
                        }
                    },
                }
            }
        }

if __name__ == "__main__":
    MysqlDatabase.run()


"""
@kopf.on.create("mysqldatabases.codemowers.io")
async def creation(name, namespace, body, **kwargs):
    target_namespace, instance, owner, api_client, api_instance, class_spec = await resolve_instance(
        namespace, name, body)
    v1 = client.CoreV1Api(api_client)

    capacity = body["spec"]["capacity"]
    replicas = class_spec["replicas"]
    routers = class_spec["routers"]

    pod_spec = class_spec.get("podSpec", {})
    storage_class = class_spec.get("storageClass", None)
    sec = Secret(target_namespace, "%s-secrets" % instance)

    if storage_class:

        # Create cluster secrets
        body = sec.wrap([{
            "key": "rootHost",
            "value": "%%"
        }, {
            "key": "rootPassword",
            "value": "%(plaintext)s"
        }, {
            "key": "rootUser",
            "value": "root",
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

        _, replica_label_selector = make_selector("mysql-innodbcluster-mysql-server", "mysql-innodbcluster-%s-mysql-server" % instance)
        _, router_label_selector = make_selector("mysql-router", "mysql-innodbcluster-%s-router" % instance)

        body = {
            "apiVersion": "mysql.oracle.com/v2",
            "kind": "InnoDBCluster",
            "metadata": {
                "namespace": target_namespace,
                "name": instance,
            },
2            "spec": {
                "tlsUseSelfSigned": True,
                "secretName": sec.name,
                "instances": replicas,
                "router": {
                    "instances": routers,
                    "podSpec": {
                        "affinity": {
                            "podAntiAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": [{
                                    "labelSelector": router_label_selector,
                                    "topologyKey": class_spec.get("topologyKey", "topology.kubernetes.io/zone")
                                }]
                            }
                        },
                    }
                },
                "datadirVolumeClaimTemplate": {
                    "storageClassName": storage_class,
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {
                        "requests": {
                            "storage": capacity,
                        }
                    }
                },
                "podSpec": {
                    **pod_spec,
                    "affinity": {
                        "podAntiAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": [{
                                "labelSelector": replica_label_selector,
                                "topologyKey": class_spec.get("topologyKey", "topology.kubernetes.io/zone")
                            }]
                        }
                    },
                }
            }
        }

        kopf.append_owner_reference(body, owner, block_owner_deletion=False)
        try:
            await api_instance.create_namespaced_custom_object(
                "mysql.oracle.com",
                "v2",
                target_namespace,
                "innodbclusters",
                body)
        except ApiException as e:
            if e.status == 409:
                logging.info("Mysql cluster %s/%s already generated" % (
                    target_namespace, instance))
            else:
                raise
        else:
            logging.info("Created Mysql cluster %s/%s" % (
                target_namespace, instance))

    # Fetch secrets to create bucket
    cluster_secrets = await v1.read_namespaced_secret(
        "%s-secrets" % instance,
        target_namespace)
    cluster_hostname = "%s.%s.svc.cluster.local" % (instance, target_namespace)
    cluster_primary = "%s-primary.%s.svc.cluster.local" % (instance, target_namespace)
    cluster_port = 3306

    conn = await aiomysql.connect(
        host=cluster_primary,
        user=b64decode(cluster_secrets.data["rootUser"]).decode("ascii"),
        password=b64decode(cluster_secrets.data["rootPassword"]).decode("ascii"),
        port=cluster_port)
    cur = await conn.cursor()

    # Create database
    user_name = database_name = ("%s_%s" % (namespace, name)).replace("-", "_")
    await cur.execute("CREATE DATABASE IF NOT EXISTS `%s`" % database_name)

    # Create secret for accessing bucket
    database_secrets = Secret(namespace, "mysql-database-%s-owner-secrets" % name)
    body = database_secrets.wrap([{
        "key": "MYSQL_HOST",
        "value": cluster_hostname,
    }, {
        "key": "MYSQL_PRIMARY",
        "value": cluster_primary,
    }, {
        "key": "MYSQL_TCP_PORT",
        "value": str(cluster_port)
    }, {
        "key": "MYSQL_USER",
        "value": user_name
    }, {
        "key": "MYSQL_PASSWORD",
        "value": "%(plaintext)s"
    }, {
        "key": "MYSQL_DATABASE",
        "value": database_name
    }, {
        "key": "DATABASE_URL",
        "value": "mysql://%s:%%(plaintext)s@%s:%d/%s" % (
            user_name, cluster_hostname, cluster_port, database_name)
    }])

    await cur.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED WITH mysql_native_password BY %s" % (
        repr(user_name), repr(database_secrets["plaintext"])))

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

    await cur.execute("GRANT ALL ON `%s`.* TO %s@'%%'" % (
        database_name, repr(user_name)))
    await cur.execute("FLUSH PRIVILEGES")

    return {"state": "READY"}


@kopf.on.delete("mysqldatabases.codemowers.io")
async def deletion(name, namespace, body, **kwargs):
    target_namespace, instance, _, api_client, api_instance, _ = await resolve_instance(
        namespace, name, body)
    v1 = client.CoreV1Api(api_client)
    try:
        await v1.delete_namespaced_secret("mysql-database-%s-owner-secrets" % name, namespace)
    except ApiException as e:
        if e.status == 404:
            pass
        else:
            raise

    # Fetch secrets to delete the database
    cluster_secrets = await v1.read_namespaced_secret(
        "%s-secrets" % instance,
        target_namespace)
    cluster_primary = "%s-primary.%s.svc.cluster.local" % (instance, target_namespace)
    cluster_port = 3306

    conn = await aiomysql.connect(
        host=cluster_primary,
        user=b64decode(cluster_secrets.data["rootUser"]).decode("ascii"),
        password=b64decode(cluster_secrets.data["rootPassword"]).decode("ascii"),
        port=cluster_port)
    cur = await conn.cursor()

    # Drop database and user
    user_name = database_name = ("%s_%s" % (namespace, name)).replace("-", "_")
    await cur.execute("DROP DATABASE IF EXISTS `%s`" % database_name)
    await cur.execute("DROP USER IF EXISTS %s" % repr(user_name))
    await cur.execute("FLUSH PRIVILEGES")


@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    if os.getenv("KUBECONFIG"):
        await config.load_kube_config()
    else:
        config.load_incluster_config()

    settings.scanning.disabled = True
    settings.posting.enabled = True
    settings.persistence.finalizer = "mysql-operator"
    logging.info("mysql-operator starting up")

#asyncio.run(kopf.operator(clusterwide=True))
"""
