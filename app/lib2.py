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
    def __init__(self):
        pass

    @classmethod
    def _run(cls):
        if os.getenv("KUBECONFIG"):
            await config.load_kube_config()
        else:
            config.load_incluster_config()
        api_client = client.ApiClient()
        api_instance = client.CustomObjectsApi(api_client)
        v1 = client.CoreV1Api(api_client)

        class_body = await api_instance.get_cluster_custom_object(
            "codemowers.io",
            "v1alpha1",
            "clusterbucketclasses",
            body["spec"]["class"])

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

class ShareableMixin():
    @classmethod
    def get_class_properties(self):
        return super(ShareableMixin, self).get_class_properties() + [
            ("targetCluster", {"type": "string"}),
        ]

class RoutedMixin:
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
