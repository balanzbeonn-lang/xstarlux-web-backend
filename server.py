from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional, Dict
import uuid
from datetime import datetime, timezone
import bcrypt
import jwt
import shutil
from fpdf import FPDF
import httpx

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Create uploads directory
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# JWT Configuration
JWT_SECRET = os.environ.get('JWT_SECRET', 'lumina-lighting-secret-key-2024')
JWT_ALGORITHM = "HS256"

# Create the main app
app = FastAPI(title="Lumina Architectural Lighting API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

security = HTTPBearer()

# ==================== MODELS ====================

# Category Model
class CategoryBase(BaseModel):
    name: str
    description: str
    image: str
    slug: str

class CategoryCreate(CategoryBase):
    pass

class Category(CategoryBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# NEW HIERARCHICAL PRODUCT STRUCTURE WITH DUAL FINISH SYSTEM

# Wattage Option with mapped dimension
class WattageOption(BaseModel):
    wattage: str  # e.g., "30", "40", "50"
    dimension: str  # e.g., "600x600x10mm" - mapped to this wattage

# Color Temperature Option with mapped lumens
class ColorTempOption(BaseModel):
    color_temp: str  # e.g., "3000", "4000", "5000"
    lumens: str  # e.g., "3000", "4000", "5000" - mapped to this color temp

# Specification model - now supports multiple options per spec
class Specification(BaseModel):
    name: str  # e.g., "Wattage", "Lumens", "Color Temperature"
    unit: Optional[str] = None  # e.g., "W", "lm", "K"
    options: List[str] = []  # Multiple values: ["20", "30", "40", "60"] for Wattage
    # For backwards compatibility, single value is also supported
    value: Optional[str] = None  # Single value (legacy, or if only one option)

# Finish Option (for outer and inner finishes)
class FinishOption(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str  # e.g., "Matte Black", "White", "Gold", "Chrome"
    color_code: Optional[str] = None  # e.g., "#000000", "#FFD700"

# Finish Combination (matrix of outer x inner with unique image)
class FinishCombination(BaseModel):
    outer_finish_id: str
    inner_finish_id: str
    image: str = ""  # Unique image for this specific combination
    sku: Optional[str] = None
    price: Optional[str] = None

# Size Variant (under a product)
class SizeVariant(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str  # e.g., "600x600mm", "1200x300mm", "Round 200mm"
    dimensions: Optional[str] = None  # e.g., "600x600x10mm"
    image: str = ""  # Default image for this size
    wire_drawing: str = ""  # Technical wire drawing / diagram for this size
    description: Optional[str] = None
    features: List[str] = []  # Features specific to this size
    # Specification structure with dependent values
    wattage_options: List[WattageOption] = []  # Wattage with mapped dimensions
    color_temp_options: List[ColorTempOption] = []  # Color temp with mapped lumens
    cri_options: List[str] = []  # CRI values: ["80+", "90+"]
    optics_options: List[str] = []  # Optics in degree: ["12°", "19°", "36°", "Sharp 38°"]
    driver_options: List[str] = []  # Driver/power cables: ["1.50m", "5m", "10m", "20m"]
    accessory_options: List[str] = []  # Accessories: ["HONEYCOMB", "None"]
    ip_rating_options: List[str] = []  # IP ratings: ["IP20", "IP44", "IP65"]
    # Legacy specifications field (for backwards compatibility)
    specifications: List[Specification] = []
    finish_combinations: List[FinishCombination] = []  # Matrix of outer x inner images

# Product (under a category)
class ProductBase(BaseModel):
    name: str
    description: str
    category_id: str
    image: str  # Main product image
    features: List[str] = []  # General product features
    outer_finishes: List[FinishOption] = []  # Outer finish options (shared across all sizes)
    inner_finishes: List[FinishOption] = []  # Inner finish options (shared across all sizes)
    size_variants: List[SizeVariant] = []

class ProductCreate(ProductBase):
    pass

class Product(ProductBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Specification Type (for admin to define spec types)
class SpecificationType(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    key: str
    unit: Optional[str] = None
    is_default: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SpecificationTypeCreate(BaseModel):
    name: str
    key: str
    unit: Optional[str] = None

# Project Models
class ProjectBase(BaseModel):
    name: str
    description: str
    category: str
    location: Optional[str] = None
    images: List[str] = []

class ProjectCreate(ProjectBase):
    pass

class Project(ProjectBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Service Models
class ServiceBase(BaseModel):
    name: str
    description: str
    image: str
    features: List[str] = []

class ServiceCreate(ServiceBase):
    pass

class Service(ServiceBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Inquiry Models
class InquiryBase(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    company: Optional[str] = None
    subject: str
    message: str
    product_id: Optional[str] = None

class InquiryCreate(InquiryBase):
    pass

class Inquiry(InquiryBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    inquiry_status: str = "new"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Auth Models
class AdminUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    name: str
    password: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    token: str
    user: dict

# ==================== HELPERS ====================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_token(user_id: str, email: str) -> str:
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc).timestamp() + 86400
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = await db.admin_users.find_one({"id": payload["user_id"]}, {"_id": 0, "password": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ==================== PUBLIC ROUTES ====================

@api_router.get("/")
async def root():
    return {"message": "Lumina Architectural Lighting API"}

# Categories (Public)
@api_router.get("/categories", response_model=List[Category])
async def get_categories():
    categories = await db.categories.find({}, {"_id": 0}).to_list(100)
    return categories

@api_router.get("/categories/{slug}", response_model=Category)
async def get_category_by_slug(slug: str):
    category = await db.categories.find_one({"$or": [{"slug": slug}, {"id": slug}]}, {"_id": 0})
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category

# Products (Public)
@api_router.get("/products", response_model=List[Product])
async def get_products(category_id: Optional[str] = None):
    query = {"category_id": category_id} if category_id else {}
    products = await db.products.find(query, {"_id": 0}).to_list(100)
    return products

@api_router.get("/products/search/query")
async def search_products(q: str = ""):
    """Search products by keyword in name, description, and features"""
    if not q or len(q.strip()) < 2:
        return []
    
    search_term = q.strip().lower()
    
    # Use MongoDB text search or regex for flexibility
    products = await db.products.find({
        "$or": [
            {"name": {"$regex": search_term, "$options": "i"}},
            {"description": {"$regex": search_term, "$options": "i"}},
            {"features": {"$elemMatch": {"$regex": search_term, "$options": "i"}}}
        ]
    }, {"_id": 0}).to_list(20)
    
    return products

@api_router.get("/products/{id}", response_model=Product)
async def get_product(id: str):
    product = await db.products.find_one({"id": id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

# Projects (Public)
@api_router.get("/projects", response_model=List[Project])
async def get_projects():
    projects = await db.projects.find({}, {"_id": 0}).to_list(100)
    return projects

@api_router.get("/projects/{id}", response_model=Project)
async def get_project(id: str):
    project = await db.projects.find_one({"id": id}, {"_id": 0})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

# Services (Public)
@api_router.get("/services", response_model=List[Service])
async def get_services():
    services = await db.services.find({}, {"_id": 0}).to_list(100)
    return services

# Inquiries (Public - Create only)
@api_router.post("/inquiries", response_model=Inquiry)
async def create_inquiry(inquiry: InquiryCreate):
    inquiry_obj = Inquiry(**inquiry.model_dump())
    doc = inquiry_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.inquiries.insert_one(doc)
    return inquiry_obj

# Specification Types (Public - Read)
@api_router.get("/specification-types", response_model=List[SpecificationType])
async def get_specification_types():
    specs = await db.specification_types.find({}, {"_id": 0}).to_list(100)
    return specs

# PDF Generation with actual image
class ConfigurationRequest(BaseModel):
    product_name: str
    size_variant_name: str
    outer_finish_name: str
    inner_finish_name: str
    specifications: List[Dict[str, str]] = []
    features: List[str] = []
    description: Optional[str] = None
    selected_image_url: Optional[str] = None
    wire_drawing_url: Optional[str] = None  # Technical drawing for the size

@api_router.post("/generate-pdf")
async def generate_configuration_pdf(config: ConfigurationRequest):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Try to download and add the product image
    image_added = False
    if config.selected_image_url:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(config.selected_image_url, timeout=10)
                if response.status_code == 200:
                    # Save temp image
                    temp_img_path = UPLOAD_DIR / f"temp_pdf_{uuid.uuid4().hex}.jpg"
                    with open(temp_img_path, 'wb') as f:
                        f.write(response.content)
                    # Add to PDF
                    pdf.image(str(temp_img_path), x=10, y=10, w=60)
                    image_added = True
                    # Clean up temp file
                    temp_img_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"Could not add image to PDF: {e}")
    
    # Title section (positioned based on whether image was added)
    y_start = 80 if image_added else 15
    pdf.set_y(y_start)
    
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(0, 12, "LUMINA ARCHITECTURAL", ln=True, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, "Product Configuration Report", ln=True, align="C")
    pdf.ln(8)
    
    # Product Details
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"Product: {config.product_name}", ln=True)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 7, f"Size: {config.size_variant_name}", ln=True)
    pdf.cell(0, 7, f"Trim Finish: {config.outer_finish_name}", ln=True)
    pdf.cell(0, 7, f"Reflector: {config.inner_finish_name}", ln=True)
    pdf.ln(5)
    
    # Specifications
    if config.specifications:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, "SPECIFICATIONS", ln=True)
        pdf.set_font("Helvetica", "", 11)
        for spec in config.specifications:
            name = spec.get('name', '')
            value = spec.get('value', '')
            unit = spec.get('unit', '')
            pdf.cell(0, 7, f"  {name}: {value}{unit}", ln=True)
        pdf.ln(5)
    
    # Features
    if config.features:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, "FEATURES", ln=True)
        pdf.set_font("Helvetica", "", 11)
        for feature in config.features:
            pdf.cell(0, 7, f"  - {feature}", ln=True)
        pdf.ln(5)
    
    # Description
    if config.description:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, "DESCRIPTION", ln=True)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, config.description)
        pdf.ln(5)
    
    # Wire Drawing / Technical Diagram
    if config.wire_drawing_url:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(config.wire_drawing_url, timeout=10)
                if response.status_code == 200:
                    # Check if we need a new page
                    if pdf.get_y() > 200:
                        pdf.add_page()
                    
                    pdf.set_font("Helvetica", "B", 12)
                    pdf.cell(0, 10, "TECHNICAL DRAWING", ln=True)
                    
                    # Save temp image
                    temp_wire_path = UPLOAD_DIR / f"temp_wire_{uuid.uuid4().hex}.jpg"
                    with open(temp_wire_path, 'wb') as f:
                        f.write(response.content)
                    
                    # Add wire drawing to PDF (centered, larger)
                    pdf.image(str(temp_wire_path), x=30, w=150)
                    
                    # Clean up temp file
                    temp_wire_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"Could not add wire drawing to PDF: {e}")
    
    # Footer
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 6, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)
    pdf.cell(0, 6, "Contact: info@xtarlux.com", ln=True)
    
    # Save PDF
    filename = f"config_{uuid.uuid4().hex[:8]}.pdf"
    filepath = UPLOAD_DIR / filename
    pdf.output(str(filepath))
    
    return FileResponse(
        path=str(filepath),
        filename=f"xTARLUX_{config.product_name.replace(' ', '_')}_Config.pdf",
        media_type="application/pdf"
    )

# ==================== AUTH ROUTES ====================

@api_router.post("/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    user = await db.admin_users.find_one({"email": request.email}, {"_id": 0})
    if not user or not verify_password(request.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_token(user["id"], user["email"])
    return TokenResponse(
        token=token,
        user={"id": user["id"], "email": user["email"], "name": user["name"]}
    )

@api_router.get("/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

# ==================== ADMIN ROUTES ====================

# Categories Admin
@api_router.get("/admin/categories", response_model=List[Category])
async def admin_get_categories(current_user: dict = Depends(get_current_user)):
    categories = await db.categories.find({}, {"_id": 0}).to_list(100)
    return categories

@api_router.post("/admin/categories", response_model=Category)
async def create_category(category: CategoryCreate, current_user: dict = Depends(get_current_user)):
    category_obj = Category(**category.model_dump())
    doc = category_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.categories.insert_one(doc)
    return category_obj

@api_router.put("/admin/categories/{category_id}", response_model=Category)
async def update_category(category_id: str, category: CategoryCreate, current_user: dict = Depends(get_current_user)):
    result = await db.categories.update_one(
        {"id": category_id},
        {"$set": category.model_dump()}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    updated = await db.categories.find_one({"id": category_id}, {"_id": 0})
    return updated

@api_router.delete("/admin/categories/{category_id}")
async def delete_category(category_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.categories.delete_one({"id": category_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"message": "Category deleted"}

# Products Admin
@api_router.get("/admin/products", response_model=List[Product])
async def admin_get_products(current_user: dict = Depends(get_current_user)):
    products = await db.products.find({}, {"_id": 0}).to_list(100)
    return products

@api_router.post("/admin/products", response_model=Product)
async def create_product(product: ProductCreate, current_user: dict = Depends(get_current_user)):
    product_obj = Product(**product.model_dump())
    doc = product_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.products.insert_one(doc)
    return product_obj

@api_router.put("/admin/products/{product_id}", response_model=Product)
async def update_product(product_id: str, product: ProductCreate, current_user: dict = Depends(get_current_user)):
    result = await db.products.update_one(
        {"id": product_id},
        {"$set": product.model_dump()}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    updated = await db.products.find_one({"id": product_id}, {"_id": 0})
    return updated

@api_router.delete("/admin/products/{product_id}")
async def delete_product(product_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.products.delete_one({"id": product_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"message": "Product deleted"}

# Projects Admin
@api_router.get("/admin/projects", response_model=List[Project])
async def admin_get_projects(current_user: dict = Depends(get_current_user)):
    projects = await db.projects.find({}, {"_id": 0}).to_list(100)
    return projects

@api_router.post("/admin/projects", response_model=Project)
async def create_project(project: ProjectCreate, current_user: dict = Depends(get_current_user)):
    project_obj = Project(**project.model_dump())
    doc = project_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.projects.insert_one(doc)
    return project_obj

@api_router.put("/admin/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, project: ProjectCreate, current_user: dict = Depends(get_current_user)):
    result = await db.projects.update_one(
        {"id": project_id},
        {"$set": project.model_dump()}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    updated = await db.projects.find_one({"id": project_id}, {"_id": 0})
    return updated

@api_router.delete("/admin/projects/{project_id}")
async def delete_project(project_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.projects.delete_one({"id": project_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"message": "Project deleted"}

# Services Admin
@api_router.get("/admin/services", response_model=List[Service])
async def admin_get_services(current_user: dict = Depends(get_current_user)):
    services = await db.services.find({}, {"_id": 0}).to_list(100)
    return services

@api_router.post("/admin/services", response_model=Service)
async def create_service(service: ServiceCreate, current_user: dict = Depends(get_current_user)):
    service_obj = Service(**service.model_dump())
    doc = service_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.services.insert_one(doc)
    return service_obj

@api_router.put("/admin/services/{service_id}", response_model=Service)
async def update_service(service_id: str, service: ServiceCreate, current_user: dict = Depends(get_current_user)):
    result = await db.services.update_one(
        {"id": service_id},
        {"$set": service.model_dump()}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Service not found")
    updated = await db.services.find_one({"id": service_id}, {"_id": 0})
    return updated

@api_router.delete("/admin/services/{service_id}")
async def delete_service(service_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.services.delete_one({"id": service_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"message": "Service deleted"}

# Inquiries Admin
@api_router.get("/admin/inquiries", response_model=List[Inquiry])
async def admin_get_inquiries(current_user: dict = Depends(get_current_user)):
    inquiries = await db.inquiries.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return inquiries

@api_router.put("/admin/inquiries/{inquiry_id}/status")
async def update_inquiry_status(inquiry_id: str, new_status: str, current_user: dict = Depends(get_current_user)):
    result = await db.inquiries.update_one(
        {"id": inquiry_id},
        {"$set": {"inquiry_status": new_status}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return {"message": "Status updated"}

@api_router.delete("/admin/inquiries/{inquiry_id}")
async def delete_inquiry(inquiry_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.inquiries.delete_one({"id": inquiry_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return {"message": "Inquiry deleted"}

# Specification Types Admin
@api_router.get("/admin/specification-types", response_model=List[SpecificationType])
async def admin_get_specification_types(current_user: dict = Depends(get_current_user)):
    specs = await db.specification_types.find({}, {"_id": 0}).to_list(100)
    return specs

@api_router.post("/admin/specification-types", response_model=SpecificationType)
async def create_specification_type(spec: SpecificationTypeCreate, current_user: dict = Depends(get_current_user)):
    key = spec.key.lower().replace(" ", "_")
    spec_obj = SpecificationType(name=spec.name, key=key, unit=spec.unit, is_default=False)
    doc = spec_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.specification_types.insert_one(doc)
    return spec_obj

@api_router.delete("/admin/specification-types/{spec_id}")
async def delete_specification_type(spec_id: str, current_user: dict = Depends(get_current_user)):
    spec = await db.specification_types.find_one({"id": spec_id}, {"_id": 0})
    if spec and spec.get("is_default"):
        raise HTTPException(status_code=400, detail="Cannot delete default specification types")
    
    result = await db.specification_types.delete_one({"id": spec_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Specification type not found")
    return {"message": "Specification type deleted"}

# File Upload
@api_router.post("/admin/upload")
async def upload_file(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="File type not allowed. Use: jpg, jpeg, png, gif, webp")
    
    unique_filename = f"{uuid.uuid4().hex}{file_ext}"
    file_path = UPLOAD_DIR / unique_filename
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {"url": f"/api/uploads/{unique_filename}", "filename": unique_filename}

# Serve uploaded files
@api_router.get("/uploads/{filename}")
async def get_uploaded_file(filename: str):
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path))

# ==================== SEED DATA ====================

@api_router.post("/seed")
async def seed_data():
    existing_categories = await db.categories.count_documents({})
    if existing_categories > 0:
        return {"message": "Data already seeded"}
    
    # Create admin user
    admin_password = hash_password("admin123")
    admin_user = {
        "id": str(uuid.uuid4()),
        "email": "admin@xtarlux.com",
        "name": "Admin User",
        "password": admin_password,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.admin_users.insert_one(admin_user)
    
    # Create default specification types
    default_specs = [
        {"id": "spec-1", "name": "Wattage", "key": "wattage", "unit": "W", "is_default": True, "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "spec-2", "name": "Lumens", "key": "lumens", "unit": "lm", "is_default": True, "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "spec-3", "name": "Color Temperature", "key": "color_temp", "unit": "K", "is_default": True, "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "spec-4", "name": "Dimensions", "key": "dimensions", "unit": "", "is_default": True, "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "spec-5", "name": "CRI", "key": "cri", "unit": "", "is_default": True, "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "spec-6", "name": "IP Rating", "key": "ip_rating", "unit": "", "is_default": True, "created_at": datetime.now(timezone.utc).isoformat()},
    ]
    await db.specification_types.insert_many(default_specs)
    
    # Categories
    categories = [
        {"id": "cat-1", "name": "Commercial Lighting", "description": "Professional lighting solutions for offices, retail spaces, and commercial buildings.", "image": "https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&q=80&w=800", "slug": "commercial", "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "cat-2", "name": "Residential Lighting", "description": "Elegant lighting designs for modern homes and living spaces.", "image": "https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?auto=format&fit=crop&q=80&w=800", "slug": "residential", "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "cat-3", "name": "Outdoor & Landscape", "description": "Weather-resistant fixtures for gardens, pathways, and outdoor areas.", "image": "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?auto=format&fit=crop&q=80&w=800", "slug": "outdoor", "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "cat-4", "name": "Architectural", "description": "Statement pieces for architectural highlights and facade illumination.", "image": "https://images.unsplash.com/photo-1486325212027-8081e485255e?auto=format&fit=crop&q=80&w=800", "slug": "architectural", "created_at": datetime.now(timezone.utc).isoformat()},
    ]
    await db.categories.insert_many(categories)
    
    # Products with new hierarchical structure (Outer Finish + Inner Finish)
    products = [
        {
            "id": "prod-1",
            "name": "LED Panel Light Pro",
            "description": "Professional LED panel with edge-lit technology for uniform light distribution.",
            "category_id": "cat-1",
            "image": "https://images.unsplash.com/photo-1560957974-f9571aa842fa?auto=format&fit=crop&q=80&w=800",
            "features": ["Flicker-free", "High CRI 90+", "5-year warranty", "Edge-lit technology"],
            "outer_finishes": [
                {"id": "outer-1", "name": "Matte White", "color_code": "#F5F5F5"},
                {"id": "outer-2", "name": "Matte Black", "color_code": "#1A1A1A"},
                {"id": "outer-3", "name": "Silver", "color_code": "#C0C0C0"}
            ],
            "inner_finishes": [
                {"id": "inner-1", "name": "White Diffuser", "color_code": "#FFFFFF"},
                {"id": "inner-2", "name": "Warm Glow", "color_code": "#FFE4B5"},
                {"id": "inner-3", "name": "Cool White", "color_code": "#E6F3FF"}
            ],
            "size_variants": [
                {
                    "id": "size-1-1",
                    "name": "Standard Panel",
                    "dimensions": "",
                    "image": "https://images.unsplash.com/photo-1560957974-f9571aa842fa?auto=format&fit=crop&q=80&w=800",
                    "wire_drawing": "",
                    "description": "Standard ceiling grid size for commercial installations",
                    "features": ["Fits standard grid ceiling", "Low profile"],
                    "wattage_options": [
                        {"wattage": "30", "dimension": "300x300x10mm"},
                        {"wattage": "40", "dimension": "600x600x10mm"},
                        {"wattage": "50", "dimension": "600x1200x10mm"}
                    ],
                    "color_temp_options": [
                        {"color_temp": "3000", "lumens": "2800"},
                        {"color_temp": "4000", "lumens": "3500"},
                        {"color_temp": "5000", "lumens": "4000"},
                        {"color_temp": "6500", "lumens": "4200"}
                    ],
                    "cri_options": ["80+", "90+"],
                    "optics_options": ["12°", "19°", "36°", "Sharp 38°", "60°"],
                    "driver_options": ["1.50m", "3m", "5m", "10m", "20m"],
                    "accessory_options": ["None", "HONEYCOMB"],
                    "ip_rating_options": ["IP20", "IP44"],
                    "specifications": [],
                    "finish_combinations": [
                        {"outer_finish_id": "outer-1", "inner_finish_id": "inner-1", "image": "https://images.unsplash.com/photo-1560957974-f9571aa842fa?auto=format&fit=crop&q=80&w=800", "sku": "PNL-600-WW", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-1", "inner_finish_id": "inner-2", "image": "https://images.unsplash.com/photo-1565814636199-ae8133055f78?auto=format&fit=crop&q=80&w=800", "sku": "PNL-600-WWG", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-2", "inner_finish_id": "inner-1", "image": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&q=80&w=800", "sku": "PNL-600-BW", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-2", "inner_finish_id": "inner-3", "image": "https://images.unsplash.com/photo-1513506003901-1e6a229e2d15?auto=format&fit=crop&q=80&w=800", "sku": "PNL-600-BC", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-3", "inner_finish_id": "inner-1", "image": "https://images.unsplash.com/photo-1524758631624-e2822e304c36?auto=format&fit=crop&q=80&w=800", "sku": "PNL-600-SW", "price": "Contact for Price"}
                    ]
                }
            ],
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "id": "prod-2",
            "name": "Pendant Chandelier",
            "description": "Modern pendant chandelier with adjustable height and elegant design.",
            "category_id": "cat-2",
            "image": "https://images.unsplash.com/photo-1540932239986-30128078f3c5?auto=format&fit=crop&q=80&w=800",
            "features": ["Adjustable height", "Dimmable", "Modern design", "Premium materials"],
            "outer_finishes": [
                {"id": "outer-p1", "name": "Matte Black", "color_code": "#1A1A1A"},
                {"id": "outer-p2", "name": "Brushed Brass", "color_code": "#B5A642"},
                {"id": "outer-p3", "name": "Chrome", "color_code": "#E8E8E8"}
            ],
            "inner_finishes": [
                {"id": "inner-p1", "name": "Gold Interior", "color_code": "#FFD700"},
                {"id": "inner-p2", "name": "White Interior", "color_code": "#FFFFFF"},
                {"id": "inner-p3", "name": "Copper Interior", "color_code": "#B87333"}
            ],
            "size_variants": [
                {
                    "id": "size-2-1",
                    "name": "Pendant Series",
                    "dimensions": "",
                    "image": "https://images.unsplash.com/photo-1540932239986-30128078f3c5?auto=format&fit=crop&q=80&w=800",
                    "wire_drawing": "",
                    "description": "Compact size perfect for dining areas",
                    "features": ["Dining room size", "Adjustable cord"],
                    "wattage_options": [
                        {"wattage": "20", "dimension": "Ø300x200mm"},
                        {"wattage": "30", "dimension": "Ø400x300mm"},
                        {"wattage": "40", "dimension": "Ø500x350mm"}
                    ],
                    "color_temp_options": [
                        {"color_temp": "2700", "lumens": "1800"},
                        {"color_temp": "3000", "lumens": "2400"}
                    ],
                    "cri_options": ["90+"],
                    "optics_options": ["24°", "36°", "60°"],
                    "driver_options": ["1.50m", "2m", "3m", "5m"],
                    "accessory_options": ["None"],
                    "ip_rating_options": ["IP20"],
                    "specifications": [],
                    "finish_combinations": [
                        {"outer_finish_id": "outer-p1", "inner_finish_id": "inner-p1", "image": "https://images.unsplash.com/photo-1540932239986-30128078f3c5?auto=format&fit=crop&q=80&w=800", "sku": "PEND-SM-BG", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-p1", "inner_finish_id": "inner-p2", "image": "https://images.unsplash.com/photo-1524484485831-a92ffc0de03f?auto=format&fit=crop&q=80&w=800", "sku": "PEND-SM-BW", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-p2", "inner_finish_id": "inner-p1", "image": "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?auto=format&fit=crop&q=80&w=800", "sku": "PEND-SM-BRG", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-p3", "inner_finish_id": "inner-p3", "image": "https://images.unsplash.com/photo-1513506003901-1e6a229e2d15?auto=format&fit=crop&q=80&w=800", "sku": "PEND-SM-CC", "price": "Contact for Price"}
                    ]
                }
            ],
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "id": "prod-3",
            "name": "Outdoor Bollard Light",
            "description": "Weather-resistant bollard light for pathways and garden areas.",
            "category_id": "cat-3",
            "image": "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?auto=format&fit=crop&q=80&w=800",
            "features": ["IP65 rated", "UV resistant", "Corrosion proof", "Energy efficient"],
            "outer_finishes": [
                {"id": "outer-b1", "name": "Anthracite", "color_code": "#383838"},
                {"id": "outer-b2", "name": "Graphite", "color_code": "#4A4A4A"},
                {"id": "outer-b3", "name": "Corten Steel", "color_code": "#8B4513"}
            ],
            "inner_finishes": [
                {"id": "inner-b1", "name": "Clear Lens", "color_code": "#E0E0E0"},
                {"id": "inner-b2", "name": "Frosted Lens", "color_code": "#F0F0F0"}
            ],
            "size_variants": [
                {
                    "id": "size-3-1",
                    "name": "Bollard Series",
                    "dimensions": "",
                    "image": "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?auto=format&fit=crop&q=80&w=800",
                    "wire_drawing": "",
                    "description": "Low-level pathway lighting",
                    "features": ["Path lighting", "Garden accent"],
                    "wattage_options": [
                        {"wattage": "8", "dimension": "Ø80x400mm"},
                        {"wattage": "10", "dimension": "Ø100x500mm"},
                        {"wattage": "15", "dimension": "Ø120x700mm"}
                    ],
                    "color_temp_options": [
                        {"color_temp": "2700", "lumens": "700"},
                        {"color_temp": "3000", "lumens": "850"},
                        {"color_temp": "4000", "lumens": "950"}
                    ],
                    "cri_options": ["80+"],
                    "optics_options": ["Diffused", "Spot 30°", "Flood 60°"],
                    "driver_options": ["2m", "5m", "10m"],
                    "accessory_options": ["None", "HONEYCOMB"],
                    "ip_rating_options": ["IP65", "IP67"],
                    "specifications": [],
                    "finish_combinations": [
                        {"outer_finish_id": "outer-b1", "inner_finish_id": "inner-b1", "image": "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?auto=format&fit=crop&q=80&w=800", "sku": "BLR-500-AC", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-b1", "inner_finish_id": "inner-b2", "image": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&q=80&w=800", "sku": "BLR-500-AF", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-b3", "inner_finish_id": "inner-b2", "image": "https://images.unsplash.com/photo-1524758631624-e2822e304c36?auto=format&fit=crop&q=80&w=800", "sku": "BLR-500-CF", "price": "Contact for Price"}
                    ]
                }
            ],
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "id": "prod-4",
            "name": "Star 55",
            "description": "Premium recessed downlight with exceptional light quality and minimal design footprint.",
            "category_id": "cat-4",
            "image": "https://images.unsplash.com/photo-1524758631624-e2822e304c36?auto=format&fit=crop&q=80&w=800",
            "features": ["Ultra-slim profile", "Premium optics", "Dimmable", "Anti-glare design"],
            "outer_finishes": [
                {"id": "outer-s1", "name": "Matte White", "color_code": "#F5F5F5"},
                {"id": "outer-s2", "name": "Matte Black", "color_code": "#1A1A1A"},
                {"id": "outer-s3", "name": "Brushed Nickel", "color_code": "#A0A0A0"}
            ],
            "inner_finishes": [
                {"id": "inner-s1", "name": "Silver Reflector", "color_code": "#C0C0C0"},
                {"id": "inner-s2", "name": "Gold Reflector", "color_code": "#FFD700"},
                {"id": "inner-s3", "name": "Black Reflector", "color_code": "#1A1A1A"}
            ],
            "size_variants": [
                {
                    "id": "size-4-1",
                    "name": "Star 55 Series",
                    "dimensions": "",
                    "image": "https://images.unsplash.com/photo-1524758631624-e2822e304c36?auto=format&fit=crop&q=80&w=800",
                    "wire_drawing": "",
                    "description": "55mm cutout recessed downlight for architectural applications",
                    "features": ["55mm cutout", "Adjustable beam", "Tool-free installation"],
                    "wattage_options": [
                        {"wattage": "7", "dimension": "Ø55x60mm"},
                        {"wattage": "10", "dimension": "Ø55x70mm"},
                        {"wattage": "15", "dimension": "Ø55x80mm"}
                    ],
                    "color_temp_options": [
                        {"color_temp": "2700", "lumens": "600"},
                        {"color_temp": "3000", "lumens": "750"},
                        {"color_temp": "4000", "lumens": "900"},
                        {"color_temp": "5000", "lumens": "1000"}
                    ],
                    "cri_options": ["90+", "97+"],
                    "optics_options": ["12°", "24°", "36°", "60°"],
                    "driver_options": ["Integrated", "1.50m Remote", "3m Remote"],
                    "accessory_options": ["None", "HONEYCOMB", "Snoot"],
                    "ip_rating_options": ["IP20", "IP44", "IP65"],
                    "specifications": [],
                    "finish_combinations": [
                        {"outer_finish_id": "outer-s1", "inner_finish_id": "inner-s1", "image": "https://images.unsplash.com/photo-1524758631624-e2822e304c36?auto=format&fit=crop&q=80&w=800", "sku": "STAR55-WS", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-s1", "inner_finish_id": "inner-s2", "image": "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?auto=format&fit=crop&q=80&w=800", "sku": "STAR55-WG", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-s2", "inner_finish_id": "inner-s1", "image": "https://images.unsplash.com/photo-1513506003901-1e6a229e2d15?auto=format&fit=crop&q=80&w=800", "sku": "STAR55-BS", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-s2", "inner_finish_id": "inner-s3", "image": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&q=80&w=800", "sku": "STAR55-BB", "price": "Contact for Price"},
                        {"outer_finish_id": "outer-s3", "inner_finish_id": "inner-s1", "image": "https://images.unsplash.com/photo-1565814636199-ae8133055f78?auto=format&fit=crop&q=80&w=800", "sku": "STAR55-NS", "price": "Contact for Price"}
                    ]
                }
            ],
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    ]
    await db.products.insert_many(products)
    
    # Projects
    projects = [
        {"id": "proj-1", "name": "Corporate HQ Illumination", "description": "Complete lighting solution for a 50-story corporate headquarters.", "category": "Commercial", "location": "New York, USA", "images": ["https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&q=80&w=800"], "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "proj-2", "name": "Luxury Villa Lighting", "description": "Custom residential lighting design for a modern luxury villa.", "category": "Residential", "location": "Miami, USA", "images": ["https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?auto=format&fit=crop&q=80&w=800"], "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "proj-3", "name": "Public Park Landscape", "description": "Sustainable landscape lighting for a 20-acre public park.", "category": "Outdoor", "location": "Seattle, USA", "images": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?auto=format&fit=crop&q=80&w=800"], "created_at": datetime.now(timezone.utc).isoformat()},
    ]
    await db.projects.insert_many(projects)
    
    # Services
    services = [
        {"id": "serv-1", "name": "Lighting Consultancy", "description": "Technical lighting consultancy to help you select the right products based on functional and aesthetic requirements.", "image": "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?auto=format&fit=crop&q=80&w=400", "features": ["Space analysis", "Product recommendations", "Technical guidance"], "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "serv-2", "name": "Product Sales", "description": "Wide range of architectural and decorative lighting products for residential, commercial, hospitality, and outdoor environments.", "image": "https://images.unsplash.com/photo-1504328345606-18bbc8c9d7d1?auto=format&fit=crop&q=80&w=400", "features": ["Architectural lighting", "Decorative luminaires", "Facade lighting"], "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "serv-3", "name": "Product Installation", "description": "Professional installation services to ensure lighting products are installed correctly and safely.", "image": "https://images.unsplash.com/photo-1621905252507-b35492cc74b4?auto=format&fit=crop&q=80&w=400", "features": ["Professional coordination", "Safety compliance", "Large-scale support"], "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "serv-4", "name": "Project Support", "description": "End-to-end project support from initial enquiry to final handover, coordinating with architects, designers, and contractors.", "image": "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&q=80&w=400", "features": ["Project coordination", "Designer collaboration", "Timeline management"], "created_at": datetime.now(timezone.utc).isoformat()},
        {"id": "serv-5", "name": "After Sales Support", "description": "Comprehensive after-sales support including product maintenance advice, warranty assistance, and spare parts. All products backed by 5 Year Warranty.", "image": "https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?auto=format&fit=crop&q=80&w=400", "features": ["5 Year Warranty", "Maintenance guidance", "Spare parts"], "created_at": datetime.now(timezone.utc).isoformat()},
    ]
    await db.services.insert_many(services)
    
    return {"message": "Data seeded successfully"}

# ==================== APP CONFIGURATION ====================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
