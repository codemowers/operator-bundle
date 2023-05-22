from strictyaml import as_document
from lib2 import ClassedOperator
from redis import Redis
from mysql import MysqlDatabase
from postgres import PostgresDatabase
from minio import Minio
from secret import Secret

for cls in (Secret, Redis, Minio, MysqlDatabase, PostgresDatabase):
    with open("../crds/%s.yml" % cls.PLURAL.lower(), "w") as fh:
        fh.write("---\n")
        fh.write(as_document(cls.generate_resource_definition()).as_yaml())
        if issubclass(cls, ClassedOperator):
            fh.write("---\n")
            fh.write(as_document(cls.generate_class_definition()).as_yaml())

        if hasattr(cls, "generate_claim_definition"):
            fh.write("---\n")
            fh.write(as_document(cls.generate_claim_definition()).as_yaml())
