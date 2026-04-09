"""
Prax Kubernetes Operator

Manages the lifecycle of PraxInstance, PraxWorkspace, and PraxSandbox custom
resources.  Built on kopf and the official kubernetes-client.

Key responsibilities:
  - Provision app deployments, TeamWork deployments, and sandbox pod pools
    when a PraxInstance is created or updated.
  - Create PVCs and assign sandbox pods when a PraxWorkspace is created.
  - Manage individual sandbox Pod lifecycle through PraxSandbox CRs.
  - Periodic health-checking and autoscaling of the sandbox pool.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Any

import httpx
import kopf
import kubernetes
from kubernetes import client, config

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

logger = logging.getLogger("prax.operator")

CRD_GROUP = "prax.ai"
CRD_VERSION = "v1alpha1"

SANDBOX_DRAIN_TIMEOUT_SECONDS = 30
HEALTH_CHECK_INTERVAL_SECONDS = 60
IDLE_SCALE_DOWN_SECONDS = 300  # 5 minutes of >50 % idle before scale-down


def _k8s_client() -> client.ApiClient:
    """Return a configured kubernetes ApiClient."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.ApiClient()


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _make_owner_ref(body: kopf.Body) -> list[dict[str, Any]]:
    """Build an ownerReferences list from the parent CR body."""
    return [
        {
            "apiVersion": f"{CRD_GROUP}/{CRD_VERSION}",
            "kind": body["kind"],
            "name": body["metadata"]["name"],
            "uid": body["metadata"]["uid"],
            "blockOwnerDeletion": True,
            "controller": True,
        }
    ]


# =========================================================================
# Helpers — child resource builders
# =========================================================================


def _build_app_deployment(
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a Deployment manifest for the Prax app."""
    replicas = spec.get("replicas", 1)
    image = spec["appImage"]
    res = (spec.get("resources") or {}).get("app", {})
    container_resources = {}
    if res.get("requests"):
        container_resources["requests"] = res["requests"]
    if res.get("limits"):
        container_resources["limits"] = res["limits"]

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": f"{name}-app",
            "namespace": namespace,
            "ownerReferences": owner_refs,
            "labels": {"app.kubernetes.io/name": "prax", "app.kubernetes.io/instance": name, "prax.ai/component": "app"},
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"prax.ai/instance": name, "prax.ai/component": "app"}},
            "template": {
                "metadata": {"labels": {"prax.ai/instance": name, "prax.ai/component": "app"}},
                "spec": {
                    "containers": [
                        {
                            "name": "prax",
                            "image": image,
                            "ports": [{"containerPort": 5000, "name": "http"}],
                            "readinessProbe": {
                                "httpGet": {"path": "/healthz/ready", "port": 5000},
                                "initialDelaySeconds": 10,
                                "periodSeconds": 15,
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "/healthz/ready", "port": 5000},
                                "initialDelaySeconds": 30,
                                "periodSeconds": 30,
                            },
                            **({"resources": container_resources} if container_resources else {}),
                            "envFrom": [{"secretRef": {"name": f"{name}-env", "optional": True}}],
                        }
                    ],
                },
            },
        },
    }


def _build_teamwork_deployment(
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a Deployment manifest for the TeamWork web UI."""
    image = spec["teamworkImage"]
    res = (spec.get("resources") or {}).get("teamwork", {})
    container_resources = {}
    if res.get("requests"):
        container_resources["requests"] = res["requests"]
    if res.get("limits"):
        container_resources["limits"] = res["limits"]

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": f"{name}-teamwork",
            "namespace": namespace,
            "ownerReferences": owner_refs,
            "labels": {"app.kubernetes.io/name": "prax", "app.kubernetes.io/instance": name, "prax.ai/component": "teamwork"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"prax.ai/instance": name, "prax.ai/component": "teamwork"}},
            "template": {
                "metadata": {"labels": {"prax.ai/instance": name, "prax.ai/component": "teamwork"}},
                "spec": {
                    "containers": [
                        {
                            "name": "teamwork",
                            "image": image,
                            "ports": [{"containerPort": 3000, "name": "http"}],
                            "readinessProbe": {
                                "httpGet": {"path": "/", "port": 3000},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 10,
                            },
                            **({"resources": container_resources} if container_resources else {}),
                        }
                    ],
                },
            },
        },
    }


def _build_app_service(
    name: str,
    namespace: str,
    owner_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": f"{name}-app",
            "namespace": namespace,
            "ownerReferences": owner_refs,
            "labels": {"prax.ai/instance": name, "prax.ai/component": "app"},
        },
        "spec": {
            "selector": {"prax.ai/instance": name, "prax.ai/component": "app"},
            "ports": [{"port": 80, "targetPort": 5000, "name": "http"}],
        },
    }


def _build_sandbox_cr(
    instance_name: str,
    namespace: str,
    index: int,
    spec: dict[str, Any],
    owner_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a PraxSandbox custom resource dict."""
    res_spec = (spec.get("resources") or {}).get("sandbox")
    sandbox_cr: dict[str, Any] = {
        "apiVersion": f"{CRD_GROUP}/{CRD_VERSION}",
        "kind": "PraxSandbox",
        "metadata": {
            "name": f"{instance_name}-sandbox-{index}",
            "namespace": namespace,
            "ownerReferences": owner_refs,
            "labels": {"prax.ai/instance": instance_name, "prax.ai/component": "sandbox"},
        },
        "spec": {
            "instanceRef": instance_name,
            "image": spec.get("sandboxImage", ""),
        },
    }
    if res_spec:
        sandbox_cr["spec"]["resources"] = res_spec
    return sandbox_cr


def _build_sandbox_pod(
    sandbox_name: str,
    namespace: str,
    image: str,
    resources: dict[str, Any] | None,
    owner_refs: list[dict[str, Any]],
    instance_name: str,
    pvc_name: str | None = None,
) -> dict[str, Any]:
    """Build the Pod manifest that backs a PraxSandbox."""
    pod_name = f"{sandbox_name}-pod"
    container: dict[str, Any] = {
        "name": "sandbox",
        "image": image,
        "ports": [{"containerPort": 8080, "name": "http"}],
        "readinessProbe": {
            "httpGet": {"path": "/healthz", "port": 8080},
            "initialDelaySeconds": 5,
            "periodSeconds": 10,
        },
    }
    if resources:
        container_resources = {}
        if resources.get("requests"):
            container_resources["requests"] = resources["requests"]
        if resources.get("limits"):
            container_resources["limits"] = resources["limits"]
        if container_resources:
            container["resources"] = container_resources

    volumes: list[dict[str, Any]] = []
    volume_mounts: list[dict[str, Any]] = []
    if pvc_name:
        volumes.append({"name": "workspace", "persistentVolumeClaim": {"claimName": pvc_name}})
        volume_mounts.append({"name": "workspace", "mountPath": "/workspace"})
    if volume_mounts:
        container["volumeMounts"] = volume_mounts

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
            "ownerReferences": owner_refs,
            "labels": {
                "prax.ai/instance": instance_name,
                "prax.ai/component": "sandbox",
                "prax.ai/sandbox": sandbox_name,
            },
        },
        "spec": {
            "restartPolicy": "Always",
            "containers": [container],
            **({"volumes": volumes} if volumes else {}),
        },
    }


def _build_workspace_pvc(
    workspace_name: str,
    namespace: str,
    storage_size: str,
    owner_refs: list[dict[str, Any]],
    instance_name: str,
    user_id: str,
) -> dict[str, Any]:
    pvc_name = f"ws-{workspace_name}"
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": pvc_name,
            "namespace": namespace,
            "ownerReferences": owner_refs,
            "labels": {
                "prax.ai/instance": instance_name,
                "prax.ai/component": "workspace",
                "prax.ai/user": user_id,
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": storage_size}},
        },
    }


# =========================================================================
# Utility — Qdrant collection management
# =========================================================================

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")


async def _create_qdrant_collection(collection_name: str) -> bool:
    """Create a Qdrant collection for a user workspace.  Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.put(
                f"{QDRANT_URL}/collections/{collection_name}",
                json={
                    "vectors": {"size": 1536, "distance": "Cosine"},
                },
            )
            if resp.status_code in (200, 201, 409):
                logger.info("Qdrant collection '%s' ensured", collection_name)
                return True
            logger.warning("Qdrant collection create returned %s: %s", resp.status_code, resp.text)
            return False
    except Exception:
        logger.exception("Failed to create Qdrant collection '%s'", collection_name)
        return False


async def _delete_qdrant_collection(collection_name: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.delete(f"{QDRANT_URL}/collections/{collection_name}")
            if resp.status_code in (200, 204, 404):
                logger.info("Qdrant collection '%s' deleted", collection_name)
                return True
            logger.warning("Qdrant collection delete returned %s: %s", resp.status_code, resp.text)
            return False
    except Exception:
        logger.exception("Failed to delete Qdrant collection '%s'", collection_name)
        return False


# =========================================================================
# Utility — CustomObject API shortcuts
# =========================================================================


def _custom_api() -> client.CustomObjectsApi:
    return client.CustomObjectsApi()


def _patch_status(plural: str, namespace: str, name: str, status: dict[str, Any]) -> None:
    """Patch the status sub-resource of a CRD instance."""
    _custom_api().patch_namespaced_custom_object_status(
        group=CRD_GROUP,
        version=CRD_VERSION,
        namespace=namespace,
        plural=plural,
        name=name,
        body={"status": status},
    )


def _set_condition(
    namespace: str,
    name: str,
    plural: str,
    cond_type: str,
    cond_status: str,
    reason: str,
    message: str,
) -> None:
    """Upsert a single condition on the resource status."""
    try:
        obj = _custom_api().get_namespaced_custom_object_status(
            group=CRD_GROUP, version=CRD_VERSION, namespace=namespace, plural=plural, name=name,
        )
    except kubernetes.client.exceptions.ApiException:
        return

    conditions = (obj.get("status") or {}).get("conditions", [])
    now = _now_iso()
    found = False
    for c in conditions:
        if c["type"] == cond_type:
            if c["status"] != cond_status:
                c["lastTransitionTime"] = now
            c["status"] = cond_status
            c["reason"] = reason
            c["message"] = message
            found = True
            break
    if not found:
        conditions.append({
            "type": cond_type,
            "status": cond_status,
            "lastTransitionTime": now,
            "reason": reason,
            "message": message,
        })
    _patch_status(plural, namespace, name, {"conditions": conditions})


# =========================================================================
# PraxInstance handlers
# =========================================================================


@kopf.on.create(CRD_GROUP, CRD_VERSION, "praxinstances")
def on_instance_create(
    body: kopf.Body,
    spec: kopf.Spec,
    name: str,
    namespace: str,
    logger: logging.Logger,
    **_: Any,
) -> dict[str, Any]:
    """Provision all child resources for a new PraxInstance."""
    logger.info("Creating PraxInstance '%s' in namespace '%s'", name, namespace)

    _k8s_client()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    owner_refs = _make_owner_ref(body)

    # --- App Deployment + Service ---
    app_dep = _build_app_deployment(name, namespace, spec, owner_refs)
    try:
        apps_v1.create_namespaced_deployment(namespace=namespace, body=app_dep)
        logger.info("Created app Deployment '%s-app'", name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 409:
            logger.info("App Deployment '%s-app' already exists, updating", name)
            apps_v1.patch_namespaced_deployment(name=f"{name}-app", namespace=namespace, body=app_dep)
        else:
            raise

    app_svc = _build_app_service(name, namespace, owner_refs)
    try:
        core_v1.create_namespaced_service(namespace=namespace, body=app_svc)
        logger.info("Created app Service '%s-app'", name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 409:
            logger.info("App Service '%s-app' already exists", name)
        else:
            raise

    # --- TeamWork Deployment ---
    tw_dep = _build_teamwork_deployment(name, namespace, spec, owner_refs)
    try:
        apps_v1.create_namespaced_deployment(namespace=namespace, body=tw_dep)
        logger.info("Created TeamWork Deployment '%s-teamwork'", name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 409:
            logger.info("TeamWork Deployment '%s-teamwork' already exists, updating", name)
            apps_v1.patch_namespaced_deployment(name=f"{name}-teamwork", namespace=namespace, body=tw_dep)
        else:
            raise

    # --- Sandbox pool ---
    sandbox_cfg = spec.get("sandbox", {})
    pool_size = sandbox_cfg.get("poolSize", 1)
    custom_api = _custom_api()
    created_sandboxes = 0

    for idx in range(pool_size):
        sb_cr = _build_sandbox_cr(name, namespace, idx, spec, owner_refs)
        try:
            custom_api.create_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural="praxsandboxes",
                body=sb_cr,
            )
            created_sandboxes += 1
            logger.info("Created PraxSandbox '%s-sandbox-%d'", name, idx)
        except kubernetes.client.exceptions.ApiException as exc:
            if exc.status == 409:
                logger.info("PraxSandbox '%s-sandbox-%d' already exists", name, idx)
                created_sandboxes += 1
            else:
                logger.exception("Failed to create PraxSandbox '%s-sandbox-%d'", name, idx)

    # --- Update status ---
    _patch_status("praxinstances", namespace, name, {
        "phase": "Running",
        "sandboxReady": 0,
        "sandboxTotal": created_sandboxes,
        "activeUsers": 0,
        "lastHealthCheck": _now_iso(),
    })
    _set_condition(namespace, name, "praxinstances", "Available", "True", "Provisioned", "All child resources created")

    logger.info("PraxInstance '%s' provisioned with %d sandboxes", name, created_sandboxes)
    return {"sandboxesCreated": created_sandboxes}


@kopf.on.update(CRD_GROUP, CRD_VERSION, "praxinstances")
def on_instance_update(
    body: kopf.Body,
    spec: kopf.Spec,
    name: str,
    namespace: str,
    old: kopf.Body,
    new: kopf.Body,
    diff: kopf.Diff,
    logger: logging.Logger,
    **_: Any,
) -> dict[str, Any]:
    """Reconcile child resources when a PraxInstance spec changes."""
    logger.info("Updating PraxInstance '%s'", name)

    _k8s_client()
    apps_v1 = client.AppsV1Api()
    owner_refs = _make_owner_ref(body)

    # Reconcile app Deployment image / replicas
    app_dep = _build_app_deployment(name, namespace, spec, owner_refs)
    try:
        apps_v1.patch_namespaced_deployment(name=f"{name}-app", namespace=namespace, body=app_dep)
        logger.info("Patched app Deployment '%s-app'", name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 404:
            apps_v1.create_namespaced_deployment(namespace=namespace, body=app_dep)
        else:
            raise

    # Reconcile TeamWork Deployment
    tw_dep = _build_teamwork_deployment(name, namespace, spec, owner_refs)
    try:
        apps_v1.patch_namespaced_deployment(name=f"{name}-teamwork", namespace=namespace, body=tw_dep)
        logger.info("Patched TeamWork Deployment '%s-teamwork'", name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 404:
            apps_v1.create_namespaced_deployment(namespace=namespace, body=tw_dep)
        else:
            raise

    # Reconcile sandbox pool size
    sandbox_cfg = spec.get("sandbox", {})
    desired_pool = sandbox_cfg.get("poolSize", 1)
    custom_api = _custom_api()

    try:
        existing = custom_api.list_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=namespace,
            plural="praxsandboxes",
            label_selector=f"prax.ai/instance={name}",
        )
        current_count = len(existing.get("items", []))
    except kubernetes.client.exceptions.ApiException:
        current_count = 0

    actions: dict[str, Any] = {"poolAdjustment": 0}

    if current_count < desired_pool:
        for idx in range(current_count, desired_pool):
            sb_cr = _build_sandbox_cr(name, namespace, idx, spec, owner_refs)
            try:
                custom_api.create_namespaced_custom_object(
                    group=CRD_GROUP,
                    version=CRD_VERSION,
                    namespace=namespace,
                    plural="praxsandboxes",
                    body=sb_cr,
                )
                logger.info("Scaled up: created PraxSandbox '%s-sandbox-%d'", name, idx)
            except kubernetes.client.exceptions.ApiException as exc:
                if exc.status != 409:
                    logger.exception("Failed to create PraxSandbox '%s-sandbox-%d'", name, idx)
        actions["poolAdjustment"] = desired_pool - current_count

    logger.info("PraxInstance '%s' reconciled", name)
    return actions


# =========================================================================
# PraxWorkspace handlers
# =========================================================================


@kopf.on.create(CRD_GROUP, CRD_VERSION, "praxworkspaces")
async def on_workspace_create(
    body: kopf.Body,
    spec: kopf.Spec,
    name: str,
    namespace: str,
    logger: logging.Logger,
    **_: Any,
) -> dict[str, Any]:
    """Provision storage and assign a sandbox for a new user workspace."""
    logger.info("Creating PraxWorkspace '%s' for user '%s'", name, spec["userId"])

    _k8s_client()
    core_v1 = client.CoreV1Api()
    custom_api = _custom_api()
    owner_refs = _make_owner_ref(body)
    user_id = spec["userId"]
    instance_name = spec["instanceRef"]
    storage_size = spec.get("storageSize", "5Gi")

    _patch_status("praxworkspaces", namespace, name, {"phase": "Provisioning"})

    # --- Create PVC ---
    pvc_manifest = _build_workspace_pvc(name, namespace, storage_size, owner_refs, instance_name, user_id)
    pvc_name = pvc_manifest["metadata"]["name"]
    try:
        core_v1.create_namespaced_persistent_volume_claim(namespace=namespace, body=pvc_manifest)
        logger.info("Created PVC '%s'", pvc_name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 409:
            logger.info("PVC '%s' already exists", pvc_name)
        else:
            _patch_status("praxworkspaces", namespace, name, {"phase": "Provisioning"})
            raise kopf.PermanentError(f"Failed to create PVC: {exc.reason}") from exc

    # --- Create Qdrant collection ---
    collection_name = f"prax-{user_id}"
    qdrant_ok = await _create_qdrant_collection(collection_name)

    # --- Find or create sandbox assignment ---
    sandbox_pod_name: str | None = None
    assigned_sandbox: str | None = None

    try:
        sandboxes = custom_api.list_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=namespace,
            plural="praxsandboxes",
            label_selector=f"prax.ai/instance={instance_name}",
        )
    except kubernetes.client.exceptions.ApiException:
        sandboxes = {"items": []}

    # Find a free (Ready, unassigned) sandbox
    for sb in sandboxes.get("items", []):
        status = sb.get("status", {})
        if status.get("phase") == "Ready" and not status.get("assignedUser"):
            assigned_sandbox = sb["metadata"]["name"]
            sandbox_pod_name = status.get("podName")
            # Mark it as Busy / assigned
            _patch_status("praxsandboxes", namespace, assigned_sandbox, {
                "phase": "Busy",
                "assignedUser": user_id,
                "lastActivity": _now_iso(),
            })
            logger.info("Assigned existing sandbox '%s' to user '%s'", assigned_sandbox, user_id)
            break

    if not assigned_sandbox:
        # Check if we can scale up
        try:
            instance_obj = custom_api.get_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural="praxinstances",
                name=instance_name,
            )
            max_pool = instance_obj.get("spec", {}).get("sandbox", {}).get("maxPoolSize", 5)
        except kubernetes.client.exceptions.ApiException:
            max_pool = 5

        current_count = len(sandboxes.get("items", []))
        if current_count < max_pool:
            new_idx = current_count
            instance_spec = instance_obj.get("spec", {})
            instance_owner_refs = _make_owner_ref(instance_obj)
            sb_cr = _build_sandbox_cr(instance_name, namespace, new_idx, instance_spec, instance_owner_refs)
            try:
                custom_api.create_namespaced_custom_object(
                    group=CRD_GROUP,
                    version=CRD_VERSION,
                    namespace=namespace,
                    plural="praxsandboxes",
                    body=sb_cr,
                )
                assigned_sandbox = sb_cr["metadata"]["name"]
                logger.info("Created new sandbox '%s' for user '%s'", assigned_sandbox, user_id)
            except kubernetes.client.exceptions.ApiException as exc:
                logger.warning("Could not create new sandbox: %s", exc.reason)
        else:
            logger.warning("Sandbox pool at max capacity (%d) for instance '%s'", max_pool, instance_name)

    # --- Update workspace status ---
    ws_status: dict[str, Any] = {
        "phase": "Ready",
        "pvcName": pvc_name,
        "lastActive": _now_iso(),
        "memoryCollections": {
            "qdrantCollection": collection_name if qdrant_ok else "",
            "neo4jNamespace": f"prax_{user_id}",
        },
    }
    if sandbox_pod_name:
        ws_status["sandboxPod"] = sandbox_pod_name
    _patch_status("praxworkspaces", namespace, name, ws_status)

    # Update instance active users count
    try:
        all_ws = custom_api.list_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=namespace,
            plural="praxworkspaces",
            label_selector=f"prax.ai/instance={instance_name}" if False else "",
        )
        active_count = sum(
            1
            for w in all_ws.get("items", [])
            if w.get("spec", {}).get("instanceRef") == instance_name
            and (w.get("status") or {}).get("phase") in ("Ready", "Provisioning")
        )
        _patch_status("praxinstances", namespace, instance_name, {"activeUsers": active_count})
    except kubernetes.client.exceptions.ApiException:
        pass

    _set_condition(namespace, name, "praxworkspaces", "Ready", "True", "Provisioned", "Workspace ready")
    logger.info("PraxWorkspace '%s' is Ready", name)
    return {"pvcName": pvc_name, "assignedSandbox": assigned_sandbox}


@kopf.on.delete(CRD_GROUP, CRD_VERSION, "praxworkspaces")
async def on_workspace_delete(
    spec: kopf.Spec,
    name: str,
    namespace: str,
    logger: logging.Logger,
    **_: Any,
) -> None:
    """Clean up sandbox assignment and Qdrant collection when a workspace is deleted."""
    logger.info("Deleting PraxWorkspace '%s'", name)

    _k8s_client()
    custom_api = _custom_api()
    user_id = spec["userId"]
    instance_name = spec["instanceRef"]

    # Release sandbox assignment
    try:
        sandboxes = custom_api.list_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=namespace,
            plural="praxsandboxes",
            label_selector=f"prax.ai/instance={instance_name}",
        )
        for sb in sandboxes.get("items", []):
            status = sb.get("status", {})
            if status.get("assignedUser") == user_id:
                sb_name = sb["metadata"]["name"]
                _patch_status("praxsandboxes", namespace, sb_name, {
                    "phase": "Ready",
                    "assignedUser": None,
                    "lastActivity": _now_iso(),
                })
                logger.info("Released sandbox '%s' from user '%s'", sb_name, user_id)
                break
    except kubernetes.client.exceptions.ApiException:
        logger.warning("Could not release sandbox assignment for workspace '%s'", name)

    # Delete Qdrant collection
    collection_name = f"prax-{user_id}"
    await _delete_qdrant_collection(collection_name)

    # Update instance active users count
    try:
        all_ws = custom_api.list_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=namespace,
            plural="praxworkspaces",
        )
        active_count = sum(
            1
            for w in all_ws.get("items", [])
            if w.get("spec", {}).get("instanceRef") == instance_name
            and w["metadata"]["name"] != name
            and (w.get("status") or {}).get("phase") in ("Ready", "Provisioning")
        )
        _patch_status("praxinstances", namespace, instance_name, {"activeUsers": active_count})
    except kubernetes.client.exceptions.ApiException:
        pass

    logger.info("PraxWorkspace '%s' cleanup complete", name)


# =========================================================================
# PraxSandbox handlers
# =========================================================================


@kopf.on.create(CRD_GROUP, CRD_VERSION, "praxsandboxes")
def on_sandbox_create(
    body: kopf.Body,
    spec: kopf.Spec,
    name: str,
    namespace: str,
    logger: logging.Logger,
    **_: Any,
) -> dict[str, Any]:
    """Create the backing Pod for a PraxSandbox."""
    logger.info("Creating PraxSandbox '%s'", name)

    _k8s_client()
    core_v1 = client.CoreV1Api()
    owner_refs = _make_owner_ref(body)
    instance_name = spec["instanceRef"]
    image = spec.get("image", "")
    resources = spec.get("resources")

    # If image not specified on sandbox, fetch from parent instance
    if not image:
        try:
            custom_api = _custom_api()
            instance = custom_api.get_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural="praxinstances",
                name=instance_name,
            )
            image = instance.get("spec", {}).get("sandboxImage", "")
        except kubernetes.client.exceptions.ApiException as exc:
            logger.error("Cannot resolve sandbox image from PraxInstance '%s'", instance_name)
            _patch_status("praxsandboxes", namespace, name, {
                "phase": "Terminated",
                "healthStatus": "unhealthy",
            })
            raise kopf.PermanentError(f"PraxInstance '{instance_name}' not found") from exc

    if not image:
        raise kopf.PermanentError("No sandbox image configured")

    _patch_status("praxsandboxes", namespace, name, {"phase": "Starting"})

    # Determine if a PVC should be mounted (if assigned to a user already)
    pvc_name: str | None = None
    assigned_user = (body.get("status") or {}).get("assignedUser")
    if assigned_user:
        pvc_name = f"ws-{instance_name}-{assigned_user}"

    pod_manifest = _build_sandbox_pod(name, namespace, image, resources, owner_refs, instance_name, pvc_name)
    pod_name = pod_manifest["metadata"]["name"]

    try:
        core_v1.create_namespaced_pod(namespace=namespace, body=pod_manifest)
        logger.info("Created sandbox Pod '%s'", pod_name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 409:
            logger.info("Sandbox Pod '%s' already exists", pod_name)
        else:
            _patch_status("praxsandboxes", namespace, name, {
                "phase": "Terminated",
                "healthStatus": "unhealthy",
            })
            raise kopf.TemporaryError(f"Failed to create pod: {exc.reason}", delay=15) from exc

    _patch_status("praxsandboxes", namespace, name, {
        "phase": "Ready",
        "podName": pod_name,
        "lastActivity": _now_iso(),
        "healthStatus": "healthy",
    })

    # Update parent instance sandbox counts
    _update_instance_sandbox_counts(namespace, instance_name, logger)

    logger.info("PraxSandbox '%s' is Ready (pod=%s)", name, pod_name)
    return {"podName": pod_name}


@kopf.on.delete(CRD_GROUP, CRD_VERSION, "praxsandboxes")
def on_sandbox_delete(
    spec: kopf.Spec,
    name: str,
    namespace: str,
    status: kopf.Status,
    logger: logging.Logger,
    **_: Any,
) -> None:
    """Drain and delete the backing Pod for a PraxSandbox."""
    logger.info("Deleting PraxSandbox '%s'", name)

    _k8s_client()
    core_v1 = client.CoreV1Api()
    pod_name = status.get("podName", f"{name}-pod")

    # Mark as draining
    try:
        _patch_status("praxsandboxes", namespace, name, {"phase": "Draining"})
    except kubernetes.client.exceptions.ApiException:
        pass  # Resource may already be gone

    # Attempt graceful drain (wait for active sessions)
    try:
        core_v1.delete_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            body=client.V1DeleteOptions(grace_period_seconds=SANDBOX_DRAIN_TIMEOUT_SECONDS),
        )
        logger.info("Deleted sandbox Pod '%s' (grace=%ds)", pod_name, SANDBOX_DRAIN_TIMEOUT_SECONDS)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 404:
            logger.info("Sandbox Pod '%s' already deleted", pod_name)
        else:
            logger.warning("Error deleting sandbox Pod '%s': %s", pod_name, exc.reason)

    # Update parent instance sandbox counts
    instance_name = spec.get("instanceRef", "")
    if instance_name:
        _update_instance_sandbox_counts(namespace, instance_name, logger)

    logger.info("PraxSandbox '%s' cleanup complete", name)


# =========================================================================
# PraxSandbox field watcher
# =========================================================================


@kopf.on.field(CRD_GROUP, CRD_VERSION, "praxsandboxes", field="status.phase")
def on_sandbox_phase_change(
    old: Any,
    new: Any,
    name: str,
    namespace: str,
    spec: kopf.Spec,
    logger: logging.Logger,
    **_: Any,
) -> None:
    """Update parent PraxInstance sandbox counts when a sandbox phase changes."""
    logger.info("PraxSandbox '%s' phase: %s -> %s", name, old, new)
    instance_name = spec.get("instanceRef", "")
    if instance_name:
        _update_instance_sandbox_counts(namespace, instance_name, logger)


def _update_instance_sandbox_counts(namespace: str, instance_name: str, logger: logging.Logger) -> None:
    """Recount sandbox Ready/Total and patch the parent PraxInstance status."""
    try:
        _k8s_client()
        custom_api = _custom_api()
        sandboxes = custom_api.list_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=namespace,
            plural="praxsandboxes",
            label_selector=f"prax.ai/instance={instance_name}",
        )
        items = sandboxes.get("items", [])
        total = len(items)
        ready = sum(1 for s in items if (s.get("status") or {}).get("phase") == "Ready")
        _patch_status("praxinstances", namespace, instance_name, {
            "sandboxReady": ready,
            "sandboxTotal": total,
        })
        logger.info("Instance '%s' sandbox counts: ready=%d total=%d", instance_name, ready, total)
    except kubernetes.client.exceptions.ApiException:
        logger.warning("Could not update sandbox counts for instance '%s'", instance_name)


# =========================================================================
# Health-check daemon
# =========================================================================


@kopf.daemon(CRD_GROUP, CRD_VERSION, "praxinstances", cancellation_timeout=10)
async def instance_health_daemon(
    spec: kopf.Spec,
    name: str,
    namespace: str,
    stopped: kopf.DaemonStopped,
    logger: logging.Logger,
    **_: Any,
) -> None:
    """Periodically health-check the Prax app and auto-scale the sandbox pool."""
    logger.info("Starting health daemon for PraxInstance '%s'", name)

    _k8s_client()
    custom_api = _custom_api()

    while not stopped:
        try:
            await _run_health_check(name, namespace, spec, custom_api, logger)
        except Exception:
            logger.exception("Health check failed for instance '%s'", name)

        # Sleep in small increments so we can respond to cancellation
        for _ in range(HEALTH_CHECK_INTERVAL_SECONDS):
            if stopped:
                break
            await asyncio.sleep(1)

    logger.info("Health daemon stopped for PraxInstance '%s'", name)


async def _run_health_check(
    name: str,
    namespace: str,
    spec: dict[str, Any],
    custom_api: client.CustomObjectsApi,
    logger: logging.Logger,
) -> None:
    """Execute one health-check + autoscale cycle."""
    app_healthy = False
    teamwork_healthy = False

    # Probe the app /healthz/ready endpoint
    app_url = f"http://{name}-app.{namespace}.svc.cluster.local/healthz/ready"
    teamwork_url = f"http://{name}-app.{namespace}.svc.cluster.local/teamwork/health"

    async with httpx.AsyncClient(timeout=10) as http:
        try:
            resp = await http.get(app_url)
            app_healthy = resp.status_code == 200
        except Exception:
            logger.debug("App health probe failed for '%s'", name)

        try:
            resp = await http.get(teamwork_url)
            teamwork_healthy = resp.status_code == 200
        except Exception:
            logger.debug("TeamWork health probe failed for '%s'", name)

    # Determine phase
    if app_healthy and teamwork_healthy:
        phase = "Running"
    elif app_healthy or teamwork_healthy:
        phase = "Degraded"
    else:
        phase = "Degraded"  # Keep Degraded rather than Failed for transient issues

    now = _now_iso()
    _patch_status("praxinstances", namespace, name, {
        "phase": phase,
        "lastHealthCheck": now,
    })

    _set_condition(
        namespace, name, "praxinstances",
        "AppHealthy",
        "True" if app_healthy else "False",
        "HealthCheckPassed" if app_healthy else "HealthCheckFailed",
        f"App health probe {'passed' if app_healthy else 'failed'} at {now}",
    )
    _set_condition(
        namespace, name, "praxinstances",
        "TeamWorkHealthy",
        "True" if teamwork_healthy else "False",
        "HealthCheckPassed" if teamwork_healthy else "HealthCheckFailed",
        f"TeamWork health probe {'passed' if teamwork_healthy else 'failed'} at {now}",
    )

    # --- Auto-scale sandbox pool ---
    sandbox_cfg = spec.get("sandbox", {})
    if not sandbox_cfg.get("scaleOnDemand", True):
        return

    pool_size = sandbox_cfg.get("poolSize", 1)
    max_pool_size = sandbox_cfg.get("maxPoolSize", 5)

    try:
        sandboxes = custom_api.list_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=namespace,
            plural="praxsandboxes",
            label_selector=f"prax.ai/instance={name}",
        )
    except kubernetes.client.exceptions.ApiException:
        return

    items = sandboxes.get("items", [])
    total = len(items)
    ready_sandboxes = [s for s in items if (s.get("status") or {}).get("phase") == "Ready"]
    busy_sandboxes = [s for s in items if (s.get("status") or {}).get("phase") == "Busy"]
    ready_count = len(ready_sandboxes)
    busy_count = len(busy_sandboxes)

    # Scale up: all sandboxes busy and room to grow
    if ready_count == 0 and busy_count > 0 and total < max_pool_size:
        new_idx = total
        try:
            instance_obj = custom_api.get_namespaced_custom_object(
                group=CRD_GROUP, version=CRD_VERSION, namespace=namespace,
                plural="praxinstances", name=name,
            )
            owner_refs = _make_owner_ref(instance_obj)
        except kubernetes.client.exceptions.ApiException:
            return

        sb_cr = _build_sandbox_cr(name, namespace, new_idx, spec, owner_refs)
        try:
            custom_api.create_namespaced_custom_object(
                group=CRD_GROUP, version=CRD_VERSION, namespace=namespace,
                plural="praxsandboxes", body=sb_cr,
            )
            logger.info("Auto-scaled up: created PraxSandbox '%s-sandbox-%d'", name, new_idx)
        except kubernetes.client.exceptions.ApiException as exc:
            if exc.status != 409:
                logger.warning("Auto-scale up failed: %s", exc.reason)

    # Scale down: >50% idle for more than 5 minutes, but stay above poolSize
    elif ready_count > 0 and total > pool_size:
        idle_ratio = ready_count / total if total > 0 else 0
        if idle_ratio > 0.5:
            # Check if idle sandboxes have been idle long enough
            now_dt = dt.datetime.now(dt.UTC)
            for sb in ready_sandboxes:
                if total <= pool_size:
                    break
                last_activity_str = (sb.get("status") or {}).get("lastActivity")
                if last_activity_str:
                    try:
                        last_activity = dt.datetime.fromisoformat(last_activity_str.replace("Z", "+00:00"))
                        idle_seconds = (now_dt - last_activity).total_seconds()
                    except (ValueError, TypeError):
                        idle_seconds = 0
                else:
                    idle_seconds = IDLE_SCALE_DOWN_SECONDS + 1  # No activity recorded = assume old

                if idle_seconds >= IDLE_SCALE_DOWN_SECONDS:
                    sb_name = sb["metadata"]["name"]
                    try:
                        custom_api.delete_namespaced_custom_object(
                            group=CRD_GROUP, version=CRD_VERSION, namespace=namespace,
                            plural="praxsandboxes", name=sb_name,
                        )
                        total -= 1
                        logger.info("Auto-scaled down: deleted idle PraxSandbox '%s'", sb_name)
                    except kubernetes.client.exceptions.ApiException as exc:
                        if exc.status != 404:
                            logger.warning("Auto-scale down failed for '%s': %s", sb_name, exc.reason)

    # Update sandbox counts
    _update_instance_sandbox_counts(namespace, name, logger)
