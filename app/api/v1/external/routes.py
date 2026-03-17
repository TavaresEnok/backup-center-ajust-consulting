"""
Endpoints da API Externa do Backup Center.

ACESSO RESTRITO — apenas 3 operações permitidas:
  1. Listar grupos (provedores)
  2. Listar backups de um grupo (somente status=success por padrão)
  3. Download de um arquivo de backup

Nenhuma informação sensível (IPs, credenciais, dados do tenant, usuários) é exposta.

Auth: Authorization: Bearer <token>
"""

import os
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, func
import uuid

from app.api.v1.external.auth import get_current_api_tenant, get_db
from app.models.tenant import Tenant
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.backup import Backup, BackupStatus

router = APIRouter()


# ---------------------------------------------------------------------------
# 1. GET /groups — Listar Grupos (Provedores/Clientes)
# ---------------------------------------------------------------------------

@router.get("/groups", summary="Listar grupos (provedores/clientes)")
def list_groups(
    tenant: Tenant = Depends(get_current_api_tenant),
    db: Session = Depends(get_db),
):
    """
    Retorna a lista de grupos cadastrados no tenant.
    Cada grupo possui um `id` necessário para consultar os backups.

    Não expõe informações sensíveis como IPs, credenciais ou dados de usuários.
    """
    groups = db.query(DeviceGroup).filter(
        DeviceGroup.tenant_id == tenant.id,
        DeviceGroup.is_active == True,
    ).order_by(DeviceGroup.name).all()

    result = []
    for g in groups:
        device_count = db.query(func.count(Device.id)).filter(
            Device.group_id == g.id,
            Device.is_active == True,
        ).scalar()

        last_backup = db.query(Backup).join(Device).filter(
            Device.group_id == g.id,
            Backup.status == BackupStatus.SUCCESS,
        ).order_by(desc(Backup.created_at)).first()

        result.append({
            "id": str(g.id),
            "name": g.name,
            "device_count": device_count,
            "last_backup_at": last_backup.created_at.isoformat() if last_backup else None,
        })

    return {
        "total": len(result),
        "groups": result,
    }


# ---------------------------------------------------------------------------
# 2. GET /groups/{group_id}/backups — Listar Backups de um Grupo
# ---------------------------------------------------------------------------

@router.get("/groups/{group_id}/backups", summary="Listar backups de um grupo")
def list_group_backups(
    group_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    status: str = Query("success", description="Filtro de status. Padrão: success"),
    tenant: Tenant = Depends(get_current_api_tenant),
    db: Session = Depends(get_db),
):
    """
    Lista os backups disponíveis para um grupo específico.

    Por padrão retorna apenas backups com `status=success`.
    Não expõe IPs, credenciais ou dados sensíveis dos dispositivos.
    """
    try:
        gid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="group_id inválido.")

    group = db.query(DeviceGroup).filter(
        DeviceGroup.id == gid,
        DeviceGroup.tenant_id == tenant.id,
        DeviceGroup.is_active == True,
    ).first()

    if not group:
        raise HTTPException(status_code=404, detail="Grupo não encontrado.")

    try:
        status_enum = BackupStatus(status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Status inválido: '{status}'. Use: success, failed, pending, in_progress"
        )

    query = db.query(Backup).options(
        joinedload(Backup.device)
    ).join(Device).filter(
        Device.group_id == gid,
        Device.tenant_id == tenant.id,
        Backup.status == status_enum,
    )

    total = query.count()
    items = query.order_by(desc(Backup.created_at)).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "group": group.name,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page if total > 0 else 0,
        "items": [
            {
                "id": str(b.id),
                "device_name": b.device.name if b.device else None,
                "status": b.status_value,
                "file_size_bytes": b.file_size_bytes,
                "hash_sha256": b.hash_sha256,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "download_url": f"/api/v1/external/backups/{b.id}/download",
            }
            for b in items
        ],
    }


# ---------------------------------------------------------------------------
# 3. GET /backups/{backup_id}/download — Download do Arquivo de Backup
# ---------------------------------------------------------------------------

@router.get("/backups/{backup_id}/download", summary="Download do arquivo de backup")
def download_backup(
    backup_id: str,
    tenant: Tenant = Depends(get_current_api_tenant),
    db: Session = Depends(get_db),
):
    """
    Faz o download do arquivo de backup.

    Valida que o backup pertence ao tenant autenticado antes de servir.
    Só permite download de backups com status 'success'.
    Não expõe metadados além do arquivo em si.
    """
    try:
        bid = uuid.UUID(backup_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="backup_id inválido.")

    backup = db.query(Backup).options(
        joinedload(Backup.device)
    ).filter(Backup.id == bid).first()

    if not backup:
        raise HTTPException(status_code=404, detail="Backup não encontrado.")

    # Garante que pertence ao tenant autenticado
    if not backup.device or str(backup.device.tenant_id) != str(tenant.id):
        raise HTTPException(status_code=404, detail="Backup não encontrado.")

    # Só permite download de backups bem-sucedidos
    if backup.status != BackupStatus.SUCCESS:
        raise HTTPException(status_code=400, detail="Apenas backups com status 'success' podem ser baixados.")

    if not backup.file_path:
        raise HTTPException(status_code=404, detail="Este backup não possui arquivo associado.")

    if os.path.isabs(backup.file_path):
        file_path = backup.file_path
    else:
        file_path = os.path.join('/app/storage/backups', backup.file_path)

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail="Arquivo de backup não encontrado no storage.",
        )

    filename = os.path.basename(file_path)
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='application/octet-stream',
    )


# ---------------------------------------------------------------------------
# 4. Catch-all para rotas não encontradas (evita 500 do Flask)
# ---------------------------------------------------------------------------

@router.get("/{path:path}", include_in_schema=False)
def api_catch_all_404(path: str):
    raise HTTPException(status_code=404, detail="Endpoint não encontrado.")
