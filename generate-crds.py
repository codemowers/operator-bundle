import yaml
from app.lib2 import ClassedBase
from app.redis import Redis
from app.mysql import MysqlDatabase
from app.postgres import PostgresDatabase
from app.minio import Bucket
from app.secret import Secret

for cls in (Secret, Redis, Bucket, MysqlDatabase, PostgresDatabase):
    with open("crds/%s.yml" % cls.PLURAL.lower(), "w") as fh:
        fh.write("---\n")
        fh.write(yaml.dump(cls.generate_resource_definition()))
        if issubclass(cls, ClassedBase):
            fh.write("---\n")
            fh.write(yaml.dump(cls.generate_class_definition()))
