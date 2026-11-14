"""Product schema (PRD section 5). Values are kept as the site shows them."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Size(BaseModel):
    label: str
    in_stock: bool


class Image(BaseModel):
    source_url: str
    position: int = Field(ge=1)
    storage_path: str | None = None


class ContentTab(BaseModel):
    label: str
    text: str
    items: list[str] = []


class Product(BaseModel):
    product_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    name: str = Field(min_length=1)
    brand: str = Field(min_length=1)
    price: float = Field(gt=0)
    original_price: float | None = None
    currency: str = Field(min_length=1)
    site_breadcrumb: list[str] = Field(min_length=1)
    spine_node_id: str | None = None
    spine_path: str | None = None
    placement_level: Literal["L3", "L4", "L5"] | None = None
    needs_placement: bool
    audience: Literal["women", "men", "kids", "unisex"]
    description: str = Field(min_length=1)
    details: list[str] = []
    attributes: dict[str, str] = {}
    content_tabs: list[ContentTab] = []
    size_and_fit: list[str] = []
    colour: str | None = None
    sizes: list[Size] = []
    in_stock: bool
    badges: list[str] = []
    images: list[Image] = Field(min_length=1)
    scraped_at: datetime
    source: Literal["json", "embedded", "html"]

    @field_validator("product_id", "url", "name", "brand", "currency", "description")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @model_validator(mode="after")
    def placement_consistent(self) -> "Product":
        if not self.spine_node_id:
            if not self.needs_placement:
                raise ValueError("needs_placement must be true when spine_node_id is empty")
            if self.spine_path or self.placement_level:
                raise ValueError("spine_path/placement_level set without spine_node_id")
        elif not (self.spine_path and self.placement_level):
            raise ValueError("spine_path and placement_level are required with spine_node_id")
        if self.original_price is not None and self.original_price <= self.price:
            raise ValueError("original_price must be above price (only set when discounted)")
        return self


REQUIRED_FIELDS = [
    name for name, f in Product.model_fields.items()
    if f.is_required() and name not in ("needs_placement", "in_stock")
] + ["needs_placement", "in_stock"]
