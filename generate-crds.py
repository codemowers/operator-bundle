import re
import yaml

PROPS_COMMON = (
  ("image", {"type": "string"}),
  ("targetNamespace", { "type": "string" }), # Do not set to create in origin namespace
  ("replicas", { "type": "integer" }),
  ("topologyKey", { "type": "string" }),
  ("podSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
)

PROPS_SHAREABLE = (
  ("targetCluster", {"type": "string"}), # Do not set to create dedicated cluster for this bucket
)

PROPS_PERSISTENT = (
  ("storageClass", { "type": "string" }),
)

PROPS_STATEFUL_SET = (
  ("secretSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
  ("podSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
  ("serviceSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
  ("headlessServiceSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
)

PROPS_INGRESS = (
  ("ingressClass", { "type": "string" }),
)

PROPS_CUSTOM_RESOURCE = (
  ("customResourceSpec", { "type": "object", "x-kubernetes-preserve-unknown-fields": True }),
)

PROPS_MONGO = PROPS_COMMON + PROPS_SHAREABLE + PROPS_PERSISTENT + PROPS_CUSTOM_RESOURCE
PROPS_POSTGRES = PROPS_COMMON + PROPS_SHAREABLE + PROPS_PERSISTENT + PROPS_CUSTOM_RESOURCE
PROPS_MYSQL = PROPS_COMMON + PROPS_SHAREABLE + PROPS_PERSISTENT + PROPS_CUSTOM_RESOURCE
PROPS_REDIS = PROPS_COMMON + PROPS_PERSISTENT + PROPS_STATEFUL_SET
PROPS_MINIO = PROPS_COMMON + PROPS_SHAREABLE + PROPS_PERSISTENT + PROPS_STATEFUL_SET + PROPS_INGRESS + \
    (("quotaType", { "type": "string", "enum": ["none", "fifo", "hard"]}),)


CLASSES = (
    ("MongoDatabase",    "MongoDatabases",    PROPS_MONGO),
    ("PostgresDatabase", "PostgresDatabases", PROPS_POSTGRES),
    ("MysqlDatabase",    "MysqlDatabases",    PROPS_MYSQL),
    ("Redis",            "Redises",           PROPS_REDIS),
    ("Bucket",           "Buckets",           PROPS_MINIO),
)

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

BASE_PRINTER_COLUMNS = [{
    "jsonPath": ".metadata.creationTimestamp",
    "name": "Age",
    "type": "date"
}, {
    "jsonPath": ".status.creation.state",
    "name": "Ready",
    "type": "string",
}]

RESOURCE_VERSIONS = [{
    "name": "v1alpha1",
    "served": True,
    "storage": True,
    "additionalPrinterColumns": BASE_PRINTER_COLUMNS + [
        {
            "name": "Capacity",
            "jsonPath": ".spec.capacity",
            "type": "string",
        }, {
            "name": "Class",
            "jsonPath": ".spec.class",
            "type": "string",
        }
    ],
    "schema": {
        "openAPIV3Schema": {
            "type": "object",
            "required": ["spec"],
            "properties": {
                "status": {
                    "type": "object",
                    "x-kubernetes-preserve-unknown-fields": True
                },
                "spec": {
                    "type": "object",
                    "required": ["capacity", "class"],
                    "properties": {
                        "capacity": {
                            "type": "string",
                            "pattern": "^[1-9][0-9]*[PTGMK]i?$"
                        },
                        "class": {
                            "type": "string",
                        }
                    }
                }
            }
        }
    }
}]

def sentence_case(string):
    if string != '':
        result = re.sub('([A-Z])', r' \1', string)
        return result[:1].upper() + result[1:].lower()
    return



def create_versions(props, name="v1alpha1"):
    props = dict(
        (("description", {"type": "string"}),) + props
    )

    printers = []
    for field in ("description", "targetNamespace", "targetCluster", "storageClass", "ingressClass"):
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


for singular, plural, props in CLASSES:
  with open("crds/%s.yml" % plural.lower(), "w") as fh:
    print("kubectl get %s -o wide" % plural)
    fh.write("---\n")
    fh.write(yaml.dump({

      "apiVersion": "apiextensions.k8s.io/v1",
      "kind": "CustomResourceDefinition",
      "metadata": {
        "name": "%s.codemowers.io" % plural.lower(),
      },
      "spec": {
        "scope": "Namespaced",
        "group": "codemowers.io",
        "names": {
          "plural": plural.lower(),
          "singular": singular.lower(),
          "kind": singular,
        },
        "versions": RESOURCE_VERSIONS,
      }

    }))
    plural = "Cluster%sClasses" % singular
    singular = "Cluster%sClass" % singular
    print("kubectl get %s -o wide" % plural)

    fh.write("---\n")
    fh.write(yaml.dump({

      "apiVersion": "apiextensions.k8s.io/v1",
      "kind": "CustomResourceDefinition",
      "metadata": {
        "name": "%s.codemowers.io" % plural.lower(),
      },
      "spec": {
        "scope": "Cluster",
        "group": "codemowers.io",
        "names": {
          "plural": plural.lower(),
          "singular": singular.lower(),
          "kind": singular,
        },
        "versions": create_versions(props),
        "conversion": {
          "strategy": "None",
        }
      }
    }))
