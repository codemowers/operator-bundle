# operator-bundle

This operator bundle wraps numerous operators for
even more simplified workflows.

This operator allows you to implement different topologies:

* Database cluster outside Kubernetes cluster, just populate the secret for the operator
* Shared database cluster in the origin namespace
* Dedicated database clusters in the origin namespace
* Shared database cluster in dedicated namespace, secret with access credentials gets written back to origin namespace
* Dedicated database clusters in dedicated namespace, same secret behaviour

The actual implementation configuration referred by the `class` attribute varies
from cluster to cluster, but you should expect something like in the snippets
provided below

# Relational databases

To order MySQL or Postgres database, note the `capacity` ends up as persistent volume size
for dedicated clusters, in case of shared clusters it has no effect:

```
---
apiVersion: codemowers.io/v1alpha1
kind: MysqlDatabase
metadata:
  name: example
spec:
  capacity: 1Gi
  class: shared
---
apiVersion: codemowers.io/v1alpha1
kind: PostgresDatabase
metadata:
  name: example
spec:
  capacity: 1Gi
  class: shared
```

# Object storage

To order S3 bucket, note the `capacity` ends up as quota for the bucket:

```
---
apiVersion: codemowers.io/v1alpha1
kind: Bucket
metadata:
  name: example
spec:
  capacity: 1Gi
  class: shared
```

# Redis

We actually instantiate KeyDB cluster as it better fits the Kubernetes world,
you might want to distinguish `persistent` and `ephemeral`
(without persistent storage) classes for Redis. Note that `capacity` ends up as
`maxmemory` configuration for the KeyDB cluster:

```
---
apiVersion: codemowers.io/v1alpha1
kind: Redis
metadata:
  name: example
spec:
  capacity: 1Gi
  class: ephemeral
```
