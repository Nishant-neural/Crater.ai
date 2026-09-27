"""Product and revision endpoints used by machine onboarding and diagnosis."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.db.models import Product, Revision
from backend.db.session import get_session

router = APIRouter(prefix="/products", tags=["products"])


class ProductCreate(BaseModel):
    manufacturer: str = Field(min_length=1, max_length=255)
    family: str = Field(min_length=1, max_length=255)
    model: str = Field(min_length=1, max_length=255)
    notes: str | None = None


class RevisionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    notes: str | None = None
    parent_revision_id: str | None = None


def _product_response(product: Product, existing: bool = False) -> dict:
    return {
        "id": product.id,
        "manufacturer": product.manufacturer,
        "family": product.family,
        "model": product.model,
        "notes": product.notes,
        "existing": existing,
    }


def _revision_response(revision: Revision, existing: bool = False) -> dict:
    return {
        "id": revision.id,
        "product_id": revision.product_id,
        "label": revision.label,
        "notes": revision.notes,
        "parent_revision_id": revision.parent_revision_id,
        "existing": existing,
    }


@router.post("")
def create_product(payload: ProductCreate, session: Session = Depends(get_session)):
    manufacturer = payload.manufacturer.strip()
    family = payload.family.strip()
    model = payload.model.strip()

    existing = (
        session.query(Product)
        .filter(Product.manufacturer == manufacturer)
        .filter(Product.family == family)
        .filter(Product.model == model)
        .first()
    )
    if existing:
        return _product_response(existing, existing=True)

    product = Product(
        manufacturer=manufacturer,
        family=family,
        model=model,
        notes=payload.notes,
    )
    session.add(product)
    session.commit()
    session.refresh(product)
    return _product_response(product)


@router.get("")
def list_products(session: Session = Depends(get_session)):
    return [
        _product_response(product)
        for product in session.query(Product).order_by(Product.manufacturer, Product.family, Product.model).all()
    ]


@router.post("/{product_id}/revisions")
def create_revision(
    product_id: str,
    payload: RevisionCreate,
    session: Session = Depends(get_session),
):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    label = payload.label.strip()
    parent_id = payload.parent_revision_id

    if parent_id:
        parent = session.get(Revision, parent_id)
        if not parent or parent.product_id != product_id:
            raise HTTPException(
                400,
                "parent_revision_id must reference a revision of the same product",
            )

    existing = (
        session.query(Revision)
        .filter(Revision.product_id == product_id)
        .filter(Revision.label == label)
        .first()
    )
    if existing:
        return _revision_response(existing, existing=True)

    revision = Revision(
        product_id=product_id,
        label=label,
        notes=payload.notes,
        parent_revision_id=parent_id,
    )
    session.add(revision)
    session.commit()
    session.refresh(revision)
    return _revision_response(revision)


@router.get("/{product_id}/revisions")
def list_revisions(product_id: str, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    return [
        _revision_response(revision)
        for revision in (
            session.query(Revision)
            .filter(Revision.product_id == product_id)
            .order_by(Revision.label)
            .all()
        )
    ]
