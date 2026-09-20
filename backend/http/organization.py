from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from turnstile_core.domain.organization import (
    OrganizationDirectory,
    OrgUnitCreate,
    OrgUnitRename,
    OrgUnitStatusUpdate,
)
from turnstile_core.services.organization import (
    OrganizationNotFoundError,
    OrganizationService,
)

from .dependencies import Repository
from .session import (
    CurrentSession,
    OwnerSession,
    require_allowed_write_origin,
    require_authenticated_session,
)

router = APIRouter(
    prefix="/api/v1/organization",
    tags=["Organization"],
    dependencies=[
        Depends(require_authenticated_session),
        Depends(require_allowed_write_origin),
    ],
)


def organization_service(repository: Repository) -> OrganizationService:
    return OrganizationService(repository)


@router.get("/directory", response_model=OrganizationDirectory)
def get_organization_directory(
    repository: Repository,
    identity: CurrentSession,
) -> OrganizationDirectory:
    del identity
    return organization_service(repository).directory()


@router.post("/departments", response_model=OrganizationDirectory, status_code=201)
def create_department(
    request: OrgUnitCreate,
    repository: Repository,
    identity: OwnerSession,
) -> OrganizationDirectory:
    try:
        return organization_service(repository).create_department(request, identity.email)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.put("/units/{unit_id}/name", response_model=OrganizationDirectory)
def rename_unit(
    unit_id: str,
    request: OrgUnitRename,
    repository: Repository,
    identity: OwnerSession,
) -> OrganizationDirectory:
    try:
        return organization_service(repository).rename(unit_id, request, identity.email)
    except OrganizationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.put("/units/{unit_id}/status", response_model=OrganizationDirectory)
def set_unit_status(
    unit_id: str,
    request: OrgUnitStatusUpdate,
    repository: Repository,
    identity: OwnerSession,
) -> OrganizationDirectory:
    try:
        return organization_service(repository).set_status(unit_id, request, identity.email)
    except OrganizationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
