#!/usr/bin/env python3

from .lib2 import IngressMixin, ShareableMixin, PersistentMixin, StatefulSetMixin, CapacityMixin, ClassedOperator

class Bucket(IngressMixin, ShareableMixin, PersistentMixin, StatefulSetMixin, CapacityMixin, ClassedOperator):
    GROUP = "codemowers.io"
    VERSION = "v1alpha1"
    SINGULAR = "Bucket"
    PLURAL = "Buckets"


import asyncio
import httpx
import kopf
import logging
import os
from base64 import b64decode
from httpx_auth import AWS4Auth
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio import client, config, utils
from .lib import Secret, make_selector, parse_capacity
from miniopy_async import MinioAdmin


@kopf.on.resume("buckets.codemowers.io")
@kopf.on.create("buckets.codemowers.io")
async def creation(name, namespace, body, **kwargs):
    logging.info("Processing %s/%s" % (namespace, name))
    api_client = client.ApiClient()
    api_instance = client.CustomObjectsApi(api_client)
    v1 = client.CoreV1Api(api_client)

    class_body = await api_instance.get_cluster_custom_object(
        "codemowers.io",
        "v1alpha1",
        "clusterbucketclasses",
        body["spec"]["class"])

    # Handle target namespace/cluster mapping
    capacity = body["spec"]["capacity"]
    expiration = body["spec"].get("expiration", 0)
    quota_type = body["spec"].get("quotaType", "hard")
    target_namespace = class_body["spec"].get("targetNamespace", namespace)
    instance = class_body["spec"].get("targetCluster", name)
    replicas = class_body["spec"]["replicas"]

    # TODO: Make sure origin namespace/name do not contain dashes,
    # or find some other trick to prevent name collisions

    # Prefix instance name with origin namespace if
    # we're hoarding instances into single namespace
    if "targetNamespace" in class_body["spec"] and "targetCluster" not in class_body["spec"]:
        instance = "%s-%s" % (namespace, instance)

    # Service hostname and FQDN
    service_name = "minio-cluster-%s" % instance
    headless_name = "%s-headless" % service_name
    service_fqdn = "%s.%s.svc.cluster.local" % (service_name, target_namespace)

    # Derive owner object for Kopf
    owner = body if target_namespace == namespace else class_body

    # Pod labels
    labels, label_selector = make_selector("minio", instance)

    # Construct secret for cluster secrets
    sec = Secret(target_namespace, "minio-cluster-%s-secrets" % instance)

    # If there is no pod spec, the Minio cluster must be outside Kubernetes cluster
    pod_spec = class_body["spec"].get("podSpec", None)
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

        # Create cluster secrets
        body = sec.wrap([{
            "key": "MINIO_ROOT_USER",
            "value": "root"
        }, {
            "key": "MINIO_ROOT_PASSWORD",
            "value": "%(plaintext)s",
        }, {
            "key": "MINIO_URI",
            "value": "http://root:%(plaintext)s@" + service_fqdn
        }, {
            "key": "AWS_S3_ENDPOINT_URL",
            "value": "http://%s" % service_fqdn
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

        # Create stateful set
        container_spec = pod_spec["containers"][0]
        container_spec["args"].append("http://%s-{0...%d}.%s.%s.svc.cluster.local/data" % (
            service_name, replicas - 1, headless_name, target_namespace))
        container_spec["envFrom"] = [{
            "secretRef": {
                "name": sec.name
            }
        }]

        body = {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {
                "namespace": target_namespace,
                "name": "minio-cluster-%s" % instance,
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
                    },
                    "spec": pod_spec,
                },
                "volumeClaimTemplates": [{
                    "metadata": {
                        "name": "data",
                    },
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "resources": {
                            "requests": {
                                "storage": capacity,
                            }
                        },
                        "storageClassName": class_body["spec"]["storageClass"],
                    }
                }]
            }
        }

        kopf.append_owner_reference(body, owner, block_owner_deletion=False)
        try:
            await utils.create_from_yaml_single_item(api_client, body)
        except ApiException as e:
            if e.status == 409:
                logging.info("Secret %(namespace)s/%(name)s already generated" % body["metadata"])
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
                    "port": 80,
                    "targetPort": 9000,
                    "name": "http",
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
                    "name": "http",
                    "port": 9000
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

    # Fetch secrets to create bucket
    logging.info("Reading minio cluster secrets %s/%s" % (target_namespace, sec.name))
    cluster_secrets = await v1.read_namespaced_secret(sec.name, target_namespace)
    minio_uri = b64decode(cluster_secrets.data["MINIO_URI"]).decode("ascii")

    # Create bucket
    bucket_name = access_key = "%s.%s" % (namespace, name)
    admin = MinioAdmin("s3",
        binary_path="/usr/bin/mc",
        env={**os.environ, "MC_HOST_s3": minio_uri})

    # Set quota
    aws = AWS4Auth(
        access_id=b64decode(cluster_secrets.data["MINIO_ROOT_USER"]).decode("ascii"),
        secret_key=b64decode(cluster_secrets.data["MINIO_ROOT_PASSWORD"]).decode("ascii"),
        region="us-east-1",
        service="s3")

    async with httpx.AsyncClient() as requests:
        base_url = "http://%s" % service_fqdn
        url = "%s/%s/" % (base_url, bucket_name)
        logging.info("Creating bucket %s with " % url)
        r = await requests.put(url, auth=aws)
        if r.status_code not in (200, 409):
            raise Exception("Creating bucket returned status code %d" % r.status_code)

        '''
        # Following returns HTTP status code 400 for some reason
        # Set expiration
        rules = """<?xml version="1.0" encoding="UTF-8"?>
          <LifecycleConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
            <Rule>
              <Expiration>
                <Days>%d</Days>
              </Expiration>
              <ID>bucket-operator</ID>
              <Filter>
                <Prefix></Prefix>
              </Filter>
              <Status>Enabled</Status>
            </Rule>
          </LifecycleConfiguration>""" % expiration

        # Set expiration
        r = await requests.put(url + "?lifecycle", auth=aws,
            headers={"Content-Type": "application/xml"},
            data="<LifecycleConfiguration>%s</LifecycleConfiguration>" % (rules if expiration else ""))
        if r.status_code not in (200,):
            raise Exception("Setting expiration for bucket returned status code %d" % r.status_code)
        '''

        # Set quota
        logging.info("Setting quota of %s to %s (%s)" % (bucket_name, capacity, quota_type))
        url = "%s/minio/admin/v3/set-bucket-quota?bucket=%s" % (base_url, bucket_name)
        r = await requests.put(url, auth=aws, json={
            "quota": parse_capacity(capacity),
            "quotatype": quota_type,
        })
        if r.status_code not in (200,):
            raise Exception("Setting quota for bucket returned status code %d" % r.status_code)

    # TODO: Add network policy
    # TODO: Add ingress

    # Create secret for accessing bucket
    bucket_secrets = Secret(namespace, "bucket-%s-owner-secrets" % name)
    body = bucket_secrets.wrap([{
        "key": "BASE_URI",
        "value": "http://%s/%s/" % (service_fqdn, bucket_name)
    }, {
        "key": "BUCKET_NAME",
        "value": bucket_name
    }, {
        "key": "AWS_S3_ENDPOINT_URL",
        "value": b64decode(cluster_secrets.data["AWS_S3_ENDPOINT_URL"]).decode("ascii")
    }, {
        "key": "AWS_DEFAULT_REGION",
        "value": "us-east-1"
    }, {
        "key": "AWS_ACCESS_KEY_ID",
        "value": access_key
    }, {
        "key": "AWS_SECRET_ACCESS_KEY",
        "value": "%(plaintext)s",
    }, {
        "key": "MINIO_URI",
        "value": "http://%s:%%(plaintext)s@%s" % (access_key, service_fqdn),
    }])

    kopf.append_owner_reference(body, owner, block_owner_deletion=False)
    try:
        await v1.create_namespaced_secret(namespace, client.V1Secret(**body))
    except ApiException as e:
        if e.status == 409:
            logging.info("Secret %s/%s already generated" % (namespace, bucket_secrets.name))
        else:
            raise
    else:
        logging.info("Created secret %s/%s" % (namespace, bucket_secrets.name))

    # Read secret again in case last run was interrupted
    secrets = await v1.read_namespaced_secret(bucket_secrets.name, namespace)
    access_key = b64decode(secrets.data["AWS_ACCESS_KEY_ID"]).decode("ascii")
    secret_key = b64decode(secrets.data["AWS_SECRET_ACCESS_KEY"]).decode("ascii")

    # Add user and set the owner read-write policy for the bucket
    logging.info("Creating user %s" % access_key)
    admin.user_add(access_key, secret_key)
    admin.policy_add("owner", "minio-owner.json")
    admin.policy_set("owner", user=access_key)

    return {"state": "READY"}


@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    if os.getenv("KUBECONFIG"):
        await config.load_kube_config()
    else:
        config.load_incluster_config()
    settings.scanning.disabled = True
    settings.posting.enabled = True
    settings.persistence.finalizer = "minio-operator"
    logging.info("minio-operator starting up")


#asyncio.run(kopf.operator(clusterwide=True))
