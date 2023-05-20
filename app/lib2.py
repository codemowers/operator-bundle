
import json
import argparse
from kubernetes_asyncio import client, config, watch
import os
import asyncio
import re
import yaml

IMMUTABLE_FIELD = {"x-kubernetes-validations": [{"message": "Value is immutable", "rule": "self == oldSelf"}]}

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
          pattern: ^([a-z0-9]([-a-z0-9]*[a-z0-9])?(\\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*/)?(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])$
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
    if string != "":
        result = re.sub("([A-Z])", r" \1", string)
        return result[:1].upper() + result[1:].lower()
    return


class Operator():
    """
    Base class for implementing Kubernetes operators in Python
    """

    async def reconcile(self):
        """
        Reconcile resources for this custom resource
        """
        print(self.generate_manifests())
        print(json.dumps(self.generate_manifests(), indent=2))

    def get_label_selector(self):
        """
        Build labels and label selector for application/instance
        """
        labels = {
            "app.kubernetes.io/name": self.__class__.__name__.lower(),
            "app.kubernetes.io/instance": self.get_target_name(),
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

    def get_annotations(self):
        """
        Add `app.kubernetes.io/managed-by` annotation to generated resources
        """
        return [
            ("app.kubernetes.io/managed-by", self.__class__.__name__.lower())
        ]

    def generate_manifests(self):
        """
        Generate array of desired Kubernetes resource manifests
        """
        return []

    def get_target_name(self):
        """
        Generate target resource name
        """
        return self.name

    def get_target_namespace(self):
        """
        Generate target namespace
        """
        return self.namespace

    def get_props(self):
        """
        Generate properties shown in the string representation of the object
        """
        return [
            ("origin_namespace", self.namespace),
            ("origin_name", self.name),
            ("target_name", self.get_target_name())
        ]

    def __repr__(self):
        """
        Return string representation of the source custom resource
        """
        return "%s(%s)" % (self.__class__.__name__, ", ".join(["%s=%s" % (k, repr(v)) for k, v in self.get_props()]))

    def __init__(self, body, dry_run=True):
        """
        Instantiate Python representation of the source custom resource
        """
        self.namespace = body["metadata"]["namespace"]
        self.name = body["metadata"]["name"]
        self.spec = body["spec"]
        self.dry_run = dry_run

    def setup(self):
        """
        Set up additional attributes for the Python representation of the source custom resource
        """
        self.labels, self.label_selector = self.get_label_selector()
        self.annotations = dict(self.get_annotations())

    @classmethod
    async def _construct_resource(cls, args, co, body):
        inst = cls(body, *args)
        inst.setup()
        return inst

    @classmethod
    def build_argument_parser(cls):
        """
        Add `--dry-run` command line argument handling
        """
        parser = argparse.ArgumentParser(description="Run %s operator" % cls.__name__)
        parser.add_argument("--dry-run", action="store_true", help="Disable state mutation")
        return parser

    @classmethod
    async def _run(cls):
        args = vars(cls.build_argument_parser().parse_args())
        if os.getenv("KUBECONFIG"):
            await config.load_kube_config()
        else:
            config.load_incluster_config()
        api_client = client.ApiClient()
        co = client.CustomObjectsApi(api_client)
        w = watch.Watch()

        async for event in w.stream(co.list_namespaced_custom_object, cls.GROUP, cls.VERSION, "", cls.PLURAL.lower()):
            body = event["object"]
            instance = await cls._construct_resource(args, co, body)
            instance.setup()
            print(instance)
            if event["type"] in ("ADDED", "MODIFIED"):
                await instance.reconcile()
            elif event["type"] == "DELETED":
                await instance.cleanup()
            else:
                print("Don't know how to handle event type", event)

    @classmethod
    def run(cls):
        """
        Run the asyncio event loop for this operator
        """
        asyncio.run(cls._run())

    @classmethod
    def get_instance_properties(cls):
        return []

    @classmethod
    def get_instance_printer_columns(cls):
        """
        Return CRD definition's printer column specification
        """
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
        """
        Generate CRD definitions for this operator
        """
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
    """
    Operator subclass for building resource class based operators

    The idea here is to tuck away impementation details into class definition keeping
    the end user custom resource as simple as possible
    """
    def __init__(self, body, class_body, **kwargs):
        super(ClassedOperator, self).__init__(body, **kwargs)
        self.class_name = class_body["metadata"]["name"]
        self.class_spec = class_body["spec"]

    def get_annotations(self):
        """
        Add `codemowers.io/class` annotation for target resources
        """
        return super(ClassedOperator, self).get_annotations() + [
            ("codemowers.io/class", self.class_name),
        ]

    def get_target_namespace(self):
        """
        Override target namespace based on `targetNamespace` property of the class
        """
        return self.class_spec.get("targetNamespace", self.namespace)

    def get_props(self):
        """
        Add `target_namespace` and `class` properties to the string representation of the Kubernetes source resource
        """
        return super(ClassedOperator, self).get_props() + [
            ("target_namespace", self.get_target_namespace()),
            ("class", self.class_name),
        ]

    @classmethod
    async def _construct_resource(cls, args, co, body):
        class_body = await co.get_cluster_custom_object(
            cls.GROUP,
            cls.VERSION,
            "cluster%sclasses" % cls.SINGULAR.lower(),
            body["spec"]["class"])
        return cls(body, class_body, **args)

    @classmethod
    def get_class_properties(cls):
        """
        Add `targetNamespace` property for the Kubernetes resource class definition
        """
        return [
            ("targetNamespace", {"type": "string", "description": "Target namespace for generated resources. Do not set to create in origin namespace."}),
            ("adminUri", {"type": "string"})
        ]

    @classmethod
    def get_instance_properties(cls):
        """
        Add `class` property for the Kubernetes resource definition
        """
        return super(ClassedOperator, cls).get_instance_properties() + [
            ("class", {"type": "string"})
        ]

    @classmethod
    def get_instance_printer_columns(cls):
        return super(ClassedOperator, cls).get_instance_printer_columns() + [{
            "name": "Class",
            "jsonPath": ".spec.class",
            "type": "string",
        }]

    @classmethod
    def generate_class_definition(cls):
        plural = "Cluster%sClasses" % cls.SINGULAR
        singular = "Cluster%sClass" % cls.SINGULAR

        def create_versions(name="v1alpha1"):
            props = dict(
                [("description", {"type": "string"})] + cls.get_class_properties()
            )
            """
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
            })"""

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
                "additionalPrinterColumns": cls.get_instance_printer_columns(),
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
    """get_label
    Integer capacity mixin for the Kubernetes resource
    """

    def get_capacity(self):
        """
        Return parsed capacity as integer
        """
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
        """
        Add `Capacity` printer column for the Kubernetes CRD definition
        """
        return super(CapacityMixin, cls).get_instance_printer_columns() + [{
            "name": "Capacity",
            "jsonPath": ".spec.capacity",
            "type": "string",
        }]

    @classmethod
    def get_instance_properties(cls):
        """
        Add `capacity` property for the Kubernetes CRD definition
        """
        return super(CapacityMixin, cls).get_instance_properties() + [
            ("capacity", {"type": "string", "pattern": "^[1-9][0-9]*[PTGMK]i?$"}),
        ]


class PersistentMixin():
    @classmethod
    def get_class_properties(self):
        """
        Add `storageClass` property for the Kubernetes CRD definition
        """
        return super(PersistentMixin, self).get_class_properties() + [
            ("storageClass", {"type": "string", **IMMUTABLE_FIELD})
        ]


class HeadlessMixin():
    """
    Mixin for handling headless Service resource
    """

    def get_headless_service_name(self):
        """
        Generate headless service name
        """
        return "%s-headless" % self.get_target_name()

    def generate_headless_service():
        raise NotImplementedError()

    def generate_manifests(self):
        """
        Generate Service manifest for headless service
        """
        return super(HeadlessMixin, self).generate_manifests() + [{
            "kind": "Service",
            "apiVersion": "v1",
            "metadata": {
                "namespace": self.get_target_namespace(),
                "name": self.get_target_name(),
                "labels": self.labels,
                "annotations": dict(self.get_annotations() + [("codemowers.io/mixin", "HeadlessMixin")]),
            },
            "spec": self.generate_headless_service()
        }]


class ServiceMixin():
    """
    Mixin for handling Service resource
    """
    def get_service_name(self):
        return self.get_target_name()

    def generate_manifests(self):
        return super(ServiceMixin, self).generate_manifests() + [{
            "kind": "Service",
            "apiVersion": "v1",
            "metadata": {
                "namespace": self.get_target_namespace(),
                "name": self.get_target_name(),
                "labels": self.labels,
                "annotations": dict(self.get_annotations() + [("codemowers.io/mixin", "ServiceMixin")]),
            },
            "spec": self.generate_headless_service()
        }]


class StatefulSetMixin():
    """
    Mixin for handling StatefulSet resource
    """
    @classmethod
    def get_class_properties(self):
        """
        Add `image`, `replicas`, `topologyKey`, `podSpec` property for the Kuberetes CRD definition
        """
        return super(StatefulSetMixin, self).get_class_properties() + [
            ("image", {"type": "string"}),
            ("replicas", {"type": "integer"}),
            ("topologyKey", {"type": "string", **IMMUTABLE_FIELD}),
            ("secretSpec", {"type": "object", "x-kubernetes-preserve-unknown-fields": True}),
            ("podSpec", {"type": "object", "x-kubernetes-preserve-unknown-fields": True}),
        ]

    def generate_stateful_set(*args, **kwargs):
        raise NotImplementedError("generate_stateful_set method required by StatefulSetMixin not implemented")

    def generate_service(*args, **kwargs):
        raise NotImplementedError("generate_service method required by StatefulSetMixin not implemented")

    def generate_manifests(self):
        manifests = super(StatefulSetMixin, self).generate_manifests()
        if self.class_spec.get("podSpec"):
            manifests.append({
                "apiVersion": "apps/v1",
                "kind": "StatefulSet",
                "metadata": {
                    "namespace": self.get_target_namespace(),
                    "name": self.get_target_name(),
                    "labels": self.labels,
                    "annotations": dict(self.get_annotations() + [("codemowers.io/mixin", "StatefulSetMixin")]),
                },
                "spec": self.generate_stateful_set()
            })
        return manifests


class ShareableMixin():
    """
    Add many source resources to one target resource handling, this can be used to implement multiple logical databases in single database cluster
    """
    def get_target_name(self):
        """code
        Generate target name for this resource
        """
        return self.class_spec.get("targetCluster", self.name)

    @classmethod
    def get_class_properties(self):
        """
        Add `targetCluster` property for the Kubernetes CRD definition
        """
        return super(ShareableMixin, self).get_class_properties() + [
            ("targetCluster", {"type": "string", **IMMUTABLE_FIELD}),
        ]


class RoutedMixin:
    """
    Handle deployment of routers and connection poolers (eg. mysql-router, pgbouncer)
    """
    @classmethod
    def get_class_properties(self):
        """
        Add `routers` and `routerPodSpec` properties for the Kubernetes CRD definition
        """
        return super(RoutedMixin, self).get_class_properties() + [
            ("routers", {"type": "integer"}),
            ("routerPodSpec", {"type": "object", "x-kubernetes-preserve-unknown-fields": True}),
        ]


class CustomResourceMixin:
    """
    Instantiate another custom resource in the target namespace
    """
    @classmethod
    def get_class_properties(self):
        """
        Add `customResourceSpec` property for the Kubernetes CRD definition
        """
        return super(CustomResourceMixin, self).get_class_properties() + [
            ("customResourceSpec", {"type": "object", "x-kubernetes-preserve-unknown-fields": True}),
        ]

    def generate_manifests(self):
        return super(CustomResourceMixin, self).generate_manifests() + [
            self.generate_custom_resource()]


class IngressMixin:
    """
    Instantiate ingress custom resource in the target namespace
    """
    @classmethod
    def get_class_properties(self):
        """
        Add `ingressClass` property for the Kubernetes CRD definition
        """
        return super(IngressMixin, self).get_class_properties() + [
            ("ingressClass", {"type": "string"})
        ]
