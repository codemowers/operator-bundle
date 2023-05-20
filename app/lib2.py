
from kubernetes_asyncio import client, config, utils, watch
import os
import asyncio
import re
import yaml

STATUS_SUBRESOURCE = yaml.load("""
properties:
  conditions:
    items:
      properties:
        lastTransitionTime:
          format: date-time
          type: string
        message:
          maxLength: 32768
          type: string
        reason:
          maxLength: 1024
          minLength: 1
          pattern: ^[A-Za-z]([A-Za-z0-9_,:]*[A-Za-z0-9_])?$
          type: string
        status:
          enum:
          - "True"
          - "False"
          - Unknown
          type: string
        type:
          maxLength: 316
          pattern: ^([a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*/)?(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])$
          type: string
      required:
      - lastTransitionTime
      - message
      - reason
      - status
      - type
      type: object
    type: array
type: object""")


def sentence_case(string):
    if string != '':
        result = re.sub('([A-Z])', r' \1', string)
        return result[:1].upper() + result[1:].lower()
    return


class Operator():
    def generate_manifests(self):
        return []

    def get_target_name(self):
        return self.name

    def get_target_namespace(self):
        return self.namespace

    def get_props(self):
        return [
            ("origin_namespace", self.namespace),
            ("origin_name", self.name),
            ("target_name", self.get_target_name())
        ]

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, ", ".join(["%s=%s" % (k,repr(v)) for k,v in self.get_props()]))


    def __init__(self, body):
        self.namespace = body["metadata"]["namespace"]
        self.name = body["metadata"]["name"]
        self.spec = body["spec"]


    @classmethod
    async def _construct_resource(cls, co, body):
        return cls(body)


    @classmethod
    async def _run(cls):
        if os.getenv("KUBECONFIG"):
            await config.load_kube_config()
        else:
            config.load_incluster_config()
        api_client = client.ApiClient()
        co = client.CustomObjectsApi(api_client)
        v1 = client.CoreV1Api(api_client)
        w = watch.Watch()

        async for event in w.stream(co.list_namespaced_custom_object, cls.GROUP, cls.VERSION, '', cls.PLURAL.lower()):
            body = event["object"]
            instance = await cls._construct_resource(co, body)
            print(instance)
            if event["type"] in ("ADDED", "MODIFIED"):
                await instance.reconcile()
            elif event["type"] == "DELETED":
                await instance.cleanup()
            else:
                print("Don't know how to handle event type", event)



    @classmethod
    def run(cls):
        asyncio.run(cls._run())

    @classmethod
    def get_instance_properties(cls):
        return []

    @classmethod
    def get_instance_printer_columns(cls):
        return [{
            "jsonPath": ".metadata.creationTimestamp",
            "name": "Age",
            "type": "date"
        }, {
            "jsonPath": ".status.creation.state",
            "name": "Ready",
            "type": "string",
        }]

    @classmethod
    def generate_resource_definition(cls):
        props = dict(cls.get_instance_properties())
        return {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {
                "name": "%s.%s" % (cls.PLURAL.lower(), cls.GROUP),
            },
            "spec": {
                "scope": "Namespaced",
                "group": cls.GROUP,
                "names": {
                    "plural": cls.PLURAL.lower(),
                    "singular": cls.SINGULAR.lower(),
                    "kind": cls.SINGULAR,
                },
                "versions": [{
                    "name": cls.VERSION,
                    "served": True,
                    "storage": True,
                    "additionalPrinterColumns": cls.get_instance_printer_columns(),
                    "schema": {
                        "openAPIV3Schema": {
                            "type": "object",
                            "required": ["spec"],
                            "properties": {
                                "status": STATUS_SUBRESOURCE,
                                "spec": {
                                    "type": "object",
                                    "required": list(props.keys()),
                                    "properties": props,
                                }
                            }
                        }
                    }
                }],
            }
        }

class ClassedOperator(Operator):
    def __init__(self, body, class_body):
        super(ClassedOperator, self).__init__(body)
        self.class_name = class_body["metadata"]["name"]
        self.class_spec = class_body["spec"]

    def get_target_namespace(self):
        return self.class_spec.get("targetNamespace", self.namespace)


    def get_props(self):
        return super(ClassedOperator, self).get_props() + [
            ("target_namespace", self.get_target_namespace()),
            ("class", self.class_name),
        ]


    @classmethod
    async def _construct_resource(cls, co, body):
        class_body = await co.get_cluster_custom_object(
            cls.GROUP,
            cls.VERSION,
            "cluster%sclasses" % cls.SINGULAR.lower(),
            body["spec"]["class"])
        return cls(body, class_body)


    @classmethod
    def get_class_properties(cls):
        return [
            ("image", {"type": "string"}),
            ("targetNamespace", { "type": "string" }), # Do not set to create in origin namespace
            ("replicas", { "type": "integer" }),
            ("topologyKey", { "type": "string" }),
            ("podSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
            ("adminUri", { "type": "string" }),
        ]


    @classmethod
    def get_instance_printer_columns(cls):
        return super(ClassedOperator, cls).get_instance_printer_columns() + [{
            "name": "Class",
            "jsonPath": ".spec.class",
            "type": "string",
        }]


    @classmethod
    def get_instance_properties(cls):
        return super(ClassedOperator, cls).get_instance_properties() + [
           ("class", { "type": "string" }),
        ]

    @classmethod
    def generate_class_definition(cls):
        plural = "Cluster%sClasses" % cls.SINGULAR
        singular = "Cluster%sClass" % cls.SINGULAR

        def create_versions(name="v1alpha1"):
            props = dict(
                [("description", {"type": "string"})] + cls.get_class_properties()
            )

            printers = []
            for field in ("description", "targetNamespace", "targetCluster", "storageClass", "ingressClass", "replicas", "routers"):
                if field in props:
                   printers.append({
                      "description": sentence_case(field),
                      "jsonPath": ".spec.%s" % field,
                      "name": sentence_case(field),
                      "type": props[field]["type"],
                   })

            if "podSpec" in props:
                printers.append({
                    "description": "Image",
                    "jsonPath": ".spec.podSpec.containers[0].image",
                    "name": "Image",
                    "type": "string"
                })
            printers.append({
                "jsonPath": ".metadata.creationTimestamp",
                "name": "Age",
                "type": "date",
                "description": "Age"
            })

            return [{
              "name": name,
              "schema": {
                "openAPIV3Schema": {
                  "required": ["spec"],
                  "properties": {
                    "spec": {
                      "properties": props,
                      "required": ["description"],
                      "type": "object",
                    },
                  },
                  "type": "object",
                },
              },
              "served": True,
              "storage": True,
              "additionalPrinterColumns": printers,
            }]

        return {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {
              "name": "%s.%s" % (plural.lower(), cls.GROUP),
            },
            "spec": {
                "scope": "Cluster",
                "group": cls.GROUP,
                "names": {
                    "plural": plural.lower(),
                    "singular": singular.lower(),
                    "kind": singular,
                },
                "versions": create_versions(),
                "conversion": {
                    "strategy": "None",
                }
            }
        }

class CapacityMixin():
    def get_capacity(self):
        return self._parse_capacity(self.spec["capacity"])

    @classmethod
    def _parse_capacity(cls, s):
        """
        Assumes the string is already validated by CRD
        """
        if s[-1] == "i":
            s = s[:-1]
            m = 1024
        else:
            m = 1000
        v, p = int(s[:-1]), s[-1]
        return v * m ** {"M": 2, "G": 3, "T": 4, "P": 5}[p]

    @classmethod
    def get_instance_printer_columns(cls):
        return super(CapacityMixin, cls).get_instance_printer_columns() + [{
            "name": "Capacity",
            "jsonPath": ".spec.capacity",
            "type": "string",
        }]

    @classmethod
    def get_instance_properties(cls):
        return super(CapacityMixin, cls).get_instance_properties() + [
            ("capacity", { "type": "string", "pattern": "^[1-9][0-9]*[PTGMK]i?$"}),
        ]

class PersistentMixin():
    @classmethod
    def get_class_properties(self):
        return super(PersistentMixin, self).get_class_properties() + [
            ("storageClass", { "type": "string" }),
        ]


class StatefulSetMixin():
    @classmethod
    def get_class_properties(self):
        return super(StatefulSetMixin, self).get_class_properties() + [
            ("secretSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
            ("podSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
            ("serviceSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
            ("headlessServiceSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
        ]


    def get_label_selector(self):
        """
        Build labels and label selector for application/instance
        """
        labels = {
            "app.kubernetes.io/name": self.__class__.__name__.lower(),
            "app.kubernetes.io/instance": self.get_target_name(),
            "codemowers.io/class": self.class_name,
        }

        expressions = []
        for key, value in labels.items():
            expressions.append({
                "key": key,
                "operator": "In",
                "values": [value]
            })

        selector = {
            "matchExpressions": expressions
        }
        return labels, selector

    def get_service_name(self):
        return self.get_target_name()

    def get_headless_service_name(self):
        return "%s-headless" % self.get_target_name()

    async def generate_stateful_set():
        raise NotImplementedError()

    async def generate_service():
        raise NotImplementedError()

    async def generate_headless_service():
        raise NotImplementedError()

    async def generate_manifests(self):
        manifests = super(StatefulSetMixin, self).generate_manifests()
        labels, label_selector = self.get_label_selector()
        manifests.append(await self.generate_headless_service(labels))
        if self.class_spec.get("podSpec"):
            manifests.append(await self.generate_stateful_set(labels, label_selector))
        manifests.append(await self.generate_service(labels))
        return manifests


class ShareableMixin():
    def get_target_name(self):
        return self.class_spec.get("targetCluster", self.name)

    @classmethod
    def get_class_properties(self):
        return super(ShareableMixin, self).get_class_properties() + [
            ("targetCluster", {"type": "string"}),
        ]

class RoutedMixin:
    """
    Handle deployment of routers (eg. mysql-router)
    """
    @classmethod
    def get_class_properties(self):
        return super(RoutedMixin, self).get_class_properties() + [
            ("routers", {"type": "integer"}),
            ("routerPodSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
        ]


class CustomResourceMixin:
    """
    Instantiate another custom resource in the target namespace
    """
    @classmethod
    def get_class_properties(self):
        return super(CustomResourceMixin, self).get_class_properties() + [
            ("customResourceSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
        ]


class IngressMixin:
    """
    Instantiate ingress custom resource in the target namespace
    """
    @classmethod
    def get_class_properties(self):
        return super(IngressMixin, self).get_class_properties() + [
            ("ingressClass", { "type": "string" }),
        ]
