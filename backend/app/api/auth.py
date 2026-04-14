"""Authentication API routes."""

import uuid
from datetime import datetime, timezone
import uuid

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_authenticated_user, get_current_user, hash_password, verify_password
from app.database import get_db
from app.models.user import Identity, User
from app.schemas.schemas import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
    IdentityBindRequest,
    IdentityUnbindRequest,
    OAuthAuthorizeResponse,
    OAuthCallbackRequest,
    TokenResponse,
    UserLogin,
    UserOut,
    UserRegister,
    UserUpdate,
    VerifyEmailRequest,
    ResendVerificationRequest,
    NeedsVerificationResponse,
    RegisterInitRequest,
    RegisterInitResponse,
    RegisterCompleteRequest,
    RegisterCompleteResponse,
    SSORegisterRequest,
    TenantChoice,
    MultiTenantResponse,
    IdentityOut,
    TenantSwitchRequest,
    TenantSwitchResponse,
)
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/registration-config")
async def get_registration_config(db: AsyncSession = Depends(get_db)):
    """Public endpoint — returns registration requirements (no auth needed)."""
    from app.models.system_settings import SystemSetting
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "invitation_code_enabled"))
    setting = result.scalar_one_or_none()
    enabled = setting.value.get("enabled", False) if setting else False
    return {"invitation_code_required": enabled}


@router.get("/check-duplicate")
async def check_duplicate(
    email: str | None = Query(None, description="Email to check"),
    username: str | None = Query(None, description="Username to check"),
    db: AsyncSession = Depends(get_db),
):
    """Check if email or username already exists."""
    from app.models.user import Identity, User
    result = {"email_exists": False, "username_exists": False, "conflicts": []}

    if email:
        # Check Identity email
        existing = await db.execute(
            select(Identity).where(Identity.email == email)
        )
        if existing.scalar_one_or_none():
            result["email_exists"] = True
            result["conflicts"].append({"type": "email", "scope": "global", "message": "Email already registered"})

    if username:
        existing = await db.execute(select(Identity).where(Identity.username == username))
        if existing.scalar_one_or_none():
            result["username_exists"] = True
            result["conflicts"].append({"type": "username", "scope": "global", "message": "Username already taken"})

    result["has_conflict"] = result["email_exists"] or result["username_exists"]
    return result


async def _send_verification_email_task(
    user: User,
    background_tasks: BackgroundTasks,
    settings: Any,
    db: AsyncSession,
) -> None:
    """Helper to create verification token and add email task to background tasks."""
    # Check if email is configured — either via DB (platform settings UI) or env vars.
    # We must check the DB config too, since most users configure SMTP via the UI.
    from app.services.system_email_service import resolve_email_config_async
    email_config = await resolve_email_config_async(db)
    if not email_config:
        logger.debug("No email config found (env or DB), skipping verification email")
        return

    from app.services.email_verification_service import email_verification_service

    try:
        # Get identity for this user
        res = await db.execute(select(Identity).where(Identity.id == user.identity_id))
        identity = res.scalar_one_or_none()

        if not identity:
            logger.warning(f"No identity found for user {user.id} ({user.email}). Cannot send verification.")
            return

        raw_code, expires_at = await email_verification_service.create_email_verification_token(identity.id, identity.email)
        expiry_minutes = int((expires_at - datetime.now(timezone.utc)).total_seconds() // 60)
        
        background_tasks.add_task(
            email_verification_service.send_verification_email,
            identity.email,
            user.display_name or identity.username or "User",
            raw_code,
            expiry_minutes,
        )
    except Exception as exc:
        logger.error(f"Failed to create verification token for {user.email}: {exc}")
        logger.warning(f"Failed to send verification email for {user.email}: {exc}")


@router.post("/register", response_model=Any, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserRegister,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Legacy registration endpoint - kept for backward compatibility.

    For new implementations, use:
    - /register/init - Step 1: Initialize registration
    - /register/sso - SSO registration
    - /verify-email - Step 3: Verify email
    """
    from app.config import get_settings
    settings = get_settings()

    # Handle SSO registration if provider info provided
    if data.provider and data.provider_code:
        return await _handle_sso_register(data, db)

    # Regular username/password registration - delegate to new flow
    return await _handle_normal_register(data, background_tasks, db, settings)
@router.post("/register/init", response_model=RegisterInitResponse, status_code=status.HTTP_201_CREATED)
async def register_init(
    data: RegisterInitRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Step 1: Initialize registration with account credentials.

    Creates/finds a global Identity and a tenant-scoped User.
    """
    from app.config import get_settings
    settings = get_settings()
    from app.services.registration_service import registration_service
    from app.models.user import Identity, User

    logger.info(f"[REGISTER_INIT] Starting registration for email={data.email}")

    # Check if this is the first user (platform admin setup)
    from sqlalchemy import func
    ident_count_result = await db.execute(select(func.count()).select_from(Identity))
    is_first_user = ident_count_result.scalar() == 0
    
    # Find or Create Identity
    identity = await registration_service.find_or_create_identity(
        db,
        email=data.email,
        username=data.username,
        password=data.password,
        is_platform_admin=is_first_user
    )

    # Defense-in-depth: verify the returned identity actually belongs to the
    # submitted email. Under normal circumstances this should never trigger
    # (find_or_create_identity no longer uses username as a lookup key), but
    # this guard protects against future regressions.
    if identity.email and identity.email != data.email:
        logger.warning(
            "[REGISTER_INIT] Identity email mismatch: submitted=%s returned=%s — rejecting",
            data.email,
            identity.email,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken. Please choose a different username.",
        )

    # If identity existed, verify password
    if identity.password_hash and not verify_password(data.password, identity.password_hash):
         raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email already registered. Incorrect password."
        )

    # For first user: auto-create/get default tenant
    tenant_uuid = None
    if is_first_user:
        from app.models.tenant import Tenant
        default = await db.execute(select(Tenant).where(Tenant.slug == "default"))
        tenant = default.scalar_one_or_none()
        if not tenant:
            tenant = Tenant(name="Default", slug="default", im_provider="web_only")
            db.add(tenant)
            await db.flush()
        tenant_uuid = tenant.id

    # Create User (tenant-scoped)
    # Check if user already exists in this tenant (if tenant_uuid is set)
    if tenant_uuid:
        existing_user_res = await db.execute(
            select(User).where(User.identity_id == identity.id, User.tenant_id == tenant_uuid)
        )
        user = existing_user_res.scalar_one_or_none()
    else:
        # Check for a "tenant-less" user (pending company setup)
        existing_user_res = await db.execute(
            select(User).where(User.identity_id == identity.id, User.tenant_id == None)
        )
        user = existing_user_res.scalar_one_or_none()

    if not user:
        user = await registration_service.create_user_with_identity(
            db,
            identity=identity,
            display_name=data.display_name or data.username,
            role="platform_admin" if is_first_user else "member",
            tenant_id=tenant_uuid,
        )
        # Set initial status
        user.is_active = is_first_user # Active immediately if first user
        user.email_verified = identity.email_verified
        await db.flush()

    # Generate token
    token = create_access_token(str(user.id), user.role)

    # Send verification email if not verified
    if not identity.email_verified:
        await _send_verification_email_task(user, background_tasks, settings, db)

    return RegisterInitResponse(
        user_id=user.id,
        email=identity.email,
        access_token=token,
        user=UserOut.model_validate(user),
        message="Registration initiated. Please verify your email." if not identity.email_verified else "Registration successful.",
        needs_company_setup=user.tenant_id is None,
        target_tenant_id=data.target_tenant_id,
    )


@router.post("/register/sso", response_model=TokenResponse)
async def register_sso(
    data: SSORegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """SSO registration - completely separate from normal registration flow.

    This endpoint handles OAuth-based registration/login via external providers.
    """
    from app.services.auth_registry import auth_provider_registry
    from app.services.registration_service import registration_service

    logger.info(f"[REGISTER_SSO] Starting SSO registration: provider={data.provider}")

    # Get provider
    auth_provider = await auth_provider_registry.get_provider(db, data.provider)
    if not auth_provider:
        raise HTTPException(status_code=400, detail=f"Provider '{data.provider}' not supported")

    # Perform SSO registration
    user, is_new, error = await registration_service.register_with_sso(
        db, data.provider, data.code, auth_provider
    )

    if error:
        raise HTTPException(status_code=400, detail=error)

    # If no tenant, check for email domain match
    if not user.tenant_id and user.email:
        tenant, _ = await registration_service.get_tenant_for_registration(
            db, email=user.email, invitation_code=data.invitation_code
        )
        if tenant:
            user.tenant_id = tenant.id
            await db.flush()

    # Generate token
    token = create_access_token(str(user.id), user.role)

    logger.info(f"[REGISTER_SSO] SSO successful: user_id={user.id}, is_new={is_new}")

    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        needs_company_setup=user.tenant_id is None,
    )


async def _handle_normal_register(data: UserRegister, background_tasks: BackgroundTasks, db: AsyncSession, settings):
    """Legacy normal registration handler."""
    logger.info(f"[REGISTER_LEGACY] email={data.email}")

    from app.services.registration_service import registration_service
    from sqlalchemy import func

    # Check if first user
    user_count_result = await db.execute(select(func.count()).select_from(User))
    is_first_user = user_count_result.scalar() == 0

    # Resolve tenant
    tenant_uuid = None
    if is_first_user:
        from app.models.tenant import Tenant
        default = await db.execute(select(Tenant).where(Tenant.slug == "default"))
        tenant = default.scalar_one_or_none()
        if not tenant:
            tenant = Tenant(name="Default", slug="default", im_provider="web_only")
            db.add(tenant)
            await db.flush()
        tenant_uuid = tenant.id
        role = "platform_admin"
    else:
        tenant, _ = await registration_service.get_tenant_for_registration(
            db, email=data.email, invitation_code=data.invitation_code
        )
        if tenant:
            tenant_uuid = tenant.id
        role = "member"

    # 1. Check for existing Identity/Tenant-User
    from app.services.registration_service import registration_service
    
    # Check if this email is already registered globally
    identity_query = select(Identity).where(Identity.email == data.email)
    ident_res = await db.execute(identity_query)
    identity = ident_res.scalar_one_or_none()

    if identity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered, please login directly."
        )
    
    # 2. Uniqueness Check (Already handled by Identity lookup above, but let's be explicit for Phone if needed)
    # conflicts = await registration_service.check_duplicate_identity(db, email=data.email)
    # ...

    # 3. Resolve or create Identity
    # If it's the first user, we auto-verify (trusted admin)
    identity = await registration_service.find_or_create_identity(
        db,
        email=data.email,
        username=data.username,
        password=data.password,
        is_platform_admin=is_first_user
    )

    # Defense-in-depth: verify the returned identity actually belongs to the
    # submitted email. Should be unreachable after the username-lookup fix, but
    # acts as a safety net against future regressions.
    if identity.email and identity.email != data.email:
        logger.warning(
            "[REGISTER_LEGACY] Identity email mismatch: submitted=%s returned=%s — rejecting",
            data.email,
            identity.email,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken. Please choose a different username.",
        )
    
    if is_first_user:
        identity.email_verified = True
        identity.is_active = True
        await db.flush()

    # 4. Create Tenant User (Handles OrgMember binding and Participant creation)
    user = await registration_service.create_user_with_identity(
        db,
        identity=identity,
        display_name=data.display_name or data.username,
        role=role,
        tenant_id=tenant_uuid,
        registration_source="web"
    )

    # Seed default agents for first user
    if is_first_user:
        await db.commit()
        try:
            from app.services.agent_seeder import seed_default_agents
            await seed_default_agents()
        except Exception as e:
            logger.warning(f"Failed to seed default agents: {e}")

    # Send verification email
    await _send_verification_email_task(user, background_tasks, settings, db)

    return RegisterInitResponse(
        user_id=user.id,
        email=user.email,
        access_token=create_access_token(str(user.id), user.role),
        user=UserOut.model_validate(user),
        message="Registration successful. Please verify your email.",
        needs_company_setup=user.tenant_id is None,
    )


async def _handle_sso_register(data: UserRegister, db: AsyncSession):
    """Legacy SSO registration handler - delegates to new SSO endpoint logic."""
    # Redirect to new SSO flow
    sso_data = SSORegisterRequest(
        provider=data.provider,
        code=data.provider_code,
        invitation_code=data.invitation_code
    )
    return await register_sso(sso_data, db)



@router.post("/login", response_model=Any)
async def login(data: UserLogin, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Login with email/phone/username and password. Supports multi-tenant selection."""
    from app.models.tenant import Tenant
    from app.models.user import Identity, User

    # 1. Query Identity
    query = select(Identity).where(
        (Identity.email == data.login_identifier) |
        (Identity.phone == data.login_identifier) |
        (Identity.username == data.login_identifier)
    )
    result = await db.execute(query)
    identity = result.scalar_one_or_none()

    if not identity or not identity.password_hash or not verify_password(data.password, identity.password_hash):
        logger.warning(f"[LOGIN] Invalid credentials for {data.login_identifier} identity_id={identity.id if identity else 'None'}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # 2. Check Global Activity & Verification
    if not identity.is_active:
         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been disabled.")

    if not identity.email_verified:
        from app.config import get_settings
        # Find any user record (just for the task)
        user_res = await db.execute(select(User).where(User.identity_id == identity.id).limit(1))
        user = user_res.scalar_one_or_none()
        
        # Trigger email delivery in background
        if user:
            await _send_verification_email_task(user, background_tasks, get_settings(), db)
        
        # Consistent with identity-first flow: Return 403 Forbidden with verification intent
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "needs_verification": True,
                "email": identity.email,
                "message": "Please verify your email to continue."
            }
        )

    # 3. Find all User records (tenants)
    result = await db.execute(select(User).where(User.identity_id == identity.id).options(selectinload(User.identity)))
    valid_users = list(result.scalars().all())

    if not valid_users:
        # User has an identity but no tenant records? Should they create one?
        # Create a "tenant-less" user if needed, or redirect to company setup
        # For now, if no users, they need company setup.
        # But wait, register_init should have created one.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No organization associated with this account.")

    # 4. Handle Tenant Selection
    if not data.tenant_id:
        # If multiple tenants, return choice
        if len(valid_users) > 1:
            tenant_ids = [u.tenant_id for u in valid_users if u.tenant_id]
            tenants_map = {}
            if tenant_ids:
                tenants_result = await db.execute(
                    select(Tenant).where(Tenant.id.in_(tenant_ids))
                )
                tenants_map = {str(t.id): t for t in tenants_result.scalars().all()}

            tenant_choices = []
            for u in valid_users:
                tenant = tenants_map.get(str(u.tenant_id)) if u.tenant_id else None
                tenant_choices.append(TenantChoice(
                    tenant_id=u.tenant_id,
                    tenant_name=tenant.name if tenant else "Create or Join Organization",
                    tenant_slug=tenant.slug if tenant else "",
                ))

            return MultiTenantResponse(
                requires_tenant_selection=True,
                login_identifier=data.login_identifier,
                tenants=tenant_choices,
            )

        # Only one tenant
        user = valid_users[0]
    else:
        # Specific tenant requested (Dedicated Link flow)
        # Search for the user record in that tenant
        user = next((u for u in valid_users if u.tenant_id == data.tenant_id), None)
        
        # Cross-tenant access check
        if not user:
             # Even platform admins must have a valid record in the targeted tenant 
             # when logging in via a dedicated tenant URL / tenant_id.
             raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account does not belong to the selected organization.",
            )


    if user.tenant_id:
        t_result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
        tenant = t_result.scalar_one_or_none()
        if tenant and not tenant.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your organization has been disabled.",
            )

    # 6. Generate Token
    token = create_access_token(str(user.id), user.role)
    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        identity=IdentityOut.model_validate(identity),
        needs_company_setup=user.tenant_id is None,
    )


@router.get("/email-hint")
async def get_email_hint(username: str, db: AsyncSession = Depends(get_db)):
    """Return a hinted email address for a given username."""
    from app.models.user import Identity
    result = await db.execute(select(Identity).where(Identity.username == username))
    identity = result.scalar_one_or_none()
    
    if not identity or not identity.email:
        raise HTTPException(status_code=404, detail="Account not found.")
        
    email = identity.email
    parts = email.split("@")
    if len(parts) == 2:
        name, domain = parts
        
        # Obfuscate name
        if len(name) <= 2:
            obs_name = name[0] + "***"
        else:
            obs_name = name[:2] + "***" + name[-1]
            
        # Obfuscate domain
        domain_parts = domain.split(".")
        if len(domain_parts) >= 2:
            d_name = domain_parts[0]
            d_ext = ".".join(domain_parts[1:])
            if len(d_name) <= 2:
                obs_domain = d_name[0] + "***." + d_ext
            else:
                obs_domain = d_name[0] + "***" + d_name[-1] + "." + d_ext
            hint = f"{obs_name}@{obs_domain}"
        else:
            hint = f"{obs_name}@{domain}"
    else:
        hint = email[:3] + "***"
        
    return {"hint": hint}


@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset link for a global Identity."""
    from app.services.system_email_service import resolve_email_config_async
    email_config = await resolve_email_config_async(db)

    if not email_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset is currently unavailable (no mail server configured)."
        )

    generic_response = {
        "ok": True,
        "message": "If an account with that email exists, a password reset email has been sent.",
    }

    # Find Identity by email
    identity_query = select(Identity).where(Identity.email == data.email)
    identity_result = await db.execute(identity_query)
    identity = identity_result.scalar_one_or_none()
    
    if not identity or not identity.is_active:
        return generic_response

    try:
        from app.services.password_reset_service import build_password_reset_url, create_password_reset_token
        from app.services.system_email_service import (
            send_password_reset_email,
        )

        raw_token, expires_at = await create_password_reset_token(identity.id)

        reset_url = await build_password_reset_url(db, raw_token)
        expiry_minutes = int((expires_at - datetime.now(timezone.utc)).total_seconds() // 60)
        background_tasks.add_task(
            send_password_reset_email,
            identity.email,
            identity.username or "User",
            reset_url,
            expiry_minutes,
        )
    except Exception as exc:
        logger.warning(f"Failed to process password reset email for {data.email}: {exc}")

    return generic_response


@router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Reset a password using a valid single-use token."""
    from app.services.password_reset_service import consume_password_reset_token

    token_data = await consume_password_reset_token(data.token)
    if not token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    identity_id = token_data["identity_id"]
    result = await db.execute(select(Identity).where(Identity.id == identity_id))
    identity = result.scalar_one_or_none()
    
    if not identity or not identity.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    new_hash = hash_password(data.new_password)
    identity.password_hash = new_hash
    
    await db.flush()
    await db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_authenticated_user)):
    """Get current user profile."""
    return UserOut.model_validate(current_user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user profile."""
    update_data = data.model_dump(exclude_unset=True)

    # Validate username uniqueness if changing
    if "username" in update_data and update_data["username"] != current_user.username:
        existing = await db.execute(
            select(User)
            .join(Identity, User.identity_id == Identity.id)
            .where(Identity.username == update_data["username"])
        )
        if existing.scalars().first():
            raise HTTPException(status_code=409, detail="Username already taken")

    # Validate email uniqueness within tenant if changing
    if "email" in update_data and update_data["email"] != current_user.email:
        existing = await db.execute(
            select(User)
            .join(Identity, User.identity_id == Identity.id)
            .where(
                Identity.email == update_data["email"],
                User.tenant_id == current_user.tenant_id,
                User.id != current_user.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

    # Validate mobile uniqueness within tenant if changing
    if "primary_mobile" in update_data and update_data["primary_mobile"] != current_user.primary_mobile:
        existing = await db.execute(
            select(User)
            .join(Identity, User.identity_id == Identity.id)
            .where(
                Identity.phone == update_data["primary_mobile"],
                User.tenant_id == current_user.tenant_id,
                User.id != current_user.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Mobile already registered")

    for field, value in update_data.items():
        setattr(current_user, field, value)
    await db.flush()

    # Sync email/phone to OrgMember if changed
    if "email" in update_data or "primary_mobile" in update_data:
        from app.services.registration_service import registration_service
        await registration_service.sync_org_member_contact_from_user(
            db,
            current_user,
            sync_email="email" in update_data,
            sync_phone="primary_mobile" in update_data,
        )

    return UserOut.model_validate(current_user)


@router.get("/my-tenants", response_model=list[TenantChoice])
async def get_my_tenants(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all tenants associated with the current user's identity."""
    from app.models.tenant import Tenant

    # 1. Get all user records for this identity
    result = await db.execute(
        select(User).where(User.identity_id == current_user.identity_id)
    )
    users = result.scalars().all()

    # 2. Extract tenant IDs
    tenant_ids = [u.tenant_id for u in users if u.tenant_id]
    if not tenant_ids:
        return []

    # 3. Get tenant details
    result = await db.execute(
        select(Tenant).where(Tenant.id.in_(tenant_ids))
    )
    tenants = result.scalars().all()

    return [
        TenantChoice(
            tenant_id=t.id,
            tenant_name=t.name,
            tenant_slug=t.slug
        ) for t in tenants
    ]


@router.post("/switch-tenant", response_model=TenantSwitchResponse)
async def switch_tenant(
    data: TenantSwitchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Switch to a different tenant and return a new token and redirect URL."""
    from app.models.tenant import Tenant
    from app.models.system_settings import SystemSetting

    # 1. Verify membership
    result = await db.execute(
        select(User).where(
            User.identity_id == current_user.identity_id,
            User.tenant_id == data.tenant_id
        )
    )
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organization."
        )

    # 2. Get tenant details
    result = await db.execute(select(Tenant).where(Tenant.id == data.tenant_id))
    tenant = result.scalar_one_or_none()

    if not tenant or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This organization is currently unavailable."
        )

    # 3. Generate new token
    token = create_access_token(str(target_user.id), target_user.role)

    # 4. Determine redirect URL
    # Determine redirect URL (Priority: sso_domain > ENV > Request > Fallback)
    from app.services.platform_service import platform_service
    redirect_url = await platform_service.get_tenant_sso_base_url(db, tenant, request)


    # Include token in redirect URL for cross-domain switching if needed
    if redirect_url:
        separator = "&" if "?" in redirect_url else "?"
        redirect_url = f"{redirect_url}{separator}token={token}"

    return TenantSwitchResponse(
        access_token=token,
        redirect_url=redirect_url,
        message="Switching organization..."
    )


@router.put("/me/password")
async def change_password(
    data: dict,
    current_user: User = Depends(get_authenticated_user),
    db: AsyncSession = Depends(get_db),
):
    """Change current user's password. Updates the global identity password."""
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")

    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="Both old_password and new_password are required")

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    # Access identity through current_user (TenantUser)
    res = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.identity)))
    user = res.scalar_one()
    identity = user.identity

    if not identity or not identity.password_hash or not verify_password(old_password, identity.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_hash = hash_password(new_password)
    identity.password_hash = new_hash
    
    await db.flush()
    await db.commit()
    return {"ok": True}


# ─── SSO/OAuth Endpoints ─────────────────────────────────────────────


@router.get("/providers")
async def list_providers(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID | None = Query(None, description="Optional tenant ID"),
):
    """List all available identity providers."""
    from app.services.auth_registry import auth_provider_registry

    providers = await auth_provider_registry.list_providers(db, str(tenant_id) if tenant_id else None)
    return [{"id": str(p.id), "provider_type": p.provider_type, "name": p.name, "is_active": p.is_active} for p in providers]


@router.get("/{provider}/authorize", response_model=OAuthAuthorizeResponse)
async def authorize(
    provider: str,
    redirect_uri: str = Query(..., description="OAuth callback URI"),
    state: str = Query("", description="CSRF state parameter"),
    db: AsyncSession = Depends(get_db),
):
    """Start OAuth authorization flow for a provider."""
    from app.services.auth_registry import auth_provider_registry
    from app.services.sso_service import sso_service

    # Get provider
    auth_provider = await auth_provider_registry.get_provider(db, provider)
    if not auth_provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not supported")

    # Generate authorization URL
    try:
        auth_url = await auth_provider.get_authorization_url(redirect_uri, state)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to generate authorization URL for {provider}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate authorization URL")

    return OAuthAuthorizeResponse(authorization_url=auth_url)


@router.post("/{provider}/callback", response_model=TokenResponse)
async def oauth_callback(
    provider: str,
    data: OAuthCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """Handle OAuth callback and login/register user."""
    from app.services.auth_registry import auth_provider_registry

    # Get provider
    auth_provider = await auth_provider_registry.get_provider(db, provider)
    if not auth_provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not supported")

    try:
        # Exchange code for token
        token_data = await auth_provider.exchange_code_for_token(data.code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to get access token from provider")

        # Get user info
        user_info = await auth_provider.get_user_info(access_token)

        # Find or create user
        user, is_new = await auth_provider.find_or_create_user(db, user_info)

        if not user:
            raise HTTPException(status_code=500, detail="Failed to create user")

        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account is disabled")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OAuth callback failed for {provider}: {e}")
        raise HTTPException(status_code=500, detail="OAuth authentication failed")

    # Generate JWT token
    jwt_token = create_access_token(str(user.id), user.role)

    return TokenResponse(
        access_token=jwt_token,
        user=UserOut.model_validate(user),
        needs_company_setup=user.tenant_id is None,
    )


@router.post("/{provider}/bind", response_model=UserOut)
async def bind_identity(
    provider: str,
    data: IdentityBindRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bind an external identity to the current user."""
    from app.services.auth_registry import auth_provider_registry
    from app.services.sso_service import sso_service

    # Get provider
    auth_provider = await auth_provider_registry.get_provider(db, provider)
    if not auth_provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not supported")

    try:
        # Exchange code for token
        token_data = await auth_provider.exchange_code_for_token(data.code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to get access token from provider")

        # Get user info
        user_info = await auth_provider.get_user_info(access_token)

        # Check if identity is already linked to another user
        existing_user = await sso_service.check_duplicate_identity(db, provider, user_info.provider_user_id)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(
                status_code=409,
                detail="This identity is already linked to another account",
            )

        # Link identity to current user
        await sso_service.link_identity(
            db,
            str(current_user.id),
            provider,
            user_info.provider_user_id,
            user_info.raw_data,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Identity bind failed for {provider}: {e}")
        raise HTTPException(status_code=500, detail="Failed to bind identity")

    return UserOut.model_validate(current_user)


@router.post("/{provider}/unbind", response_model=UserOut)
async def unbind_identity(
    provider: str,
    data: IdentityUnbindRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unlink an external identity from the current user."""
    from app.services.sso_service import sso_service

    # Unlink identity
    success = await sso_service.unlink_identity(db, str(current_user.id), provider)
    if not success:
        raise HTTPException(status_code=404, detail=f"No linked identity found for provider '{provider}'")

    return UserOut.model_validate(current_user)


# ─── Email Verification Endpoints ──────────────────────────────────────


@router.post("/verify-email")
async def verify_email(data: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    """Verify email address using a token from the verification email.

    On success, returns user info and access token to allow immediate login.
    """
    from app.services.email_verification_service import email_verification_service

    token_data = await email_verification_service.consume_email_verification_token(data.token)
    if not token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    identity_id = token_data.get("identity_id")
    if not identity_id:
        raise HTTPException(status_code=400, detail="Token does not contain identity information")
    
    # 1. Update Identity
    identity_result = await db.execute(select(Identity).where(Identity.id == identity_id))
    identity = identity_result.scalar_one_or_none()
    if not identity:
        raise HTTPException(status_code=400, detail="Identity not found")

    identity.email_verified = True
    identity.is_active = True
    
    # 2. Activate all linked User accounts
    # email_verified is a proxy to Identity, so only update physical is_active column
    from sqlalchemy import update
    await db.execute(
        update(User)
        .where(User.identity_id == identity.id)
        .values(is_active=True)
    )
    
    await db.flush()
    await db.commit()

    # Refresh after commit to avoid MissingGreenlet during Pydantic validation
    await db.refresh(identity)

    # 3. Find a representative user for the token (for immediate login)
    user_result = await db.execute(
        select(User)
        .where(User.identity_id == identity.id)
        .order_by(User.created_at.desc())
        .limit(1)
    )
    user = user_result.scalar_one_or_none()

    # 4. Generate token and return full response for Auto Login (TokenResponse)
    effective_id = str(user.id) if user else str(identity.id)
    effective_role = user.role if user else "user"
    token = create_access_token(effective_id, effective_role)

    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user) if user else None,
        identity=IdentityOut.model_validate(identity),
        needs_company_setup=user.tenant_id is None if user else True,
    )


@router.post("/resend-verification")
async def resend_verification(
    data: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Resend email verification link."""
    from app.services.system_email_service import resolve_email_config_async

    # Always return success to prevent email enumeration
    generic_response = {
        "ok": True,
        "message": "If an account with that email exists, a verification email has been sent.",
    }

    # Check if email is configured (DB-only, no env fallback)
    email_config = await resolve_email_config_async(db)
    if not email_config:
        return generic_response

    # Find Identity by email
    id_result = await db.execute(select(Identity).where(Identity.email == data.email))
    identity = id_result.scalar_one_or_none()

    # Don't reveal if user exists or already verified
    if not identity or identity.email_verified:
        return generic_response

    # Pick a representative user context (e.g. latest one)
    u_result = await db.execute(
        select(User).where(User.identity_id == identity.id).order_by(User.created_at.desc()).limit(1)
    )
    user = u_result.scalar_one_or_none()
    
    if user:
        await _send_verification_email_task(user, background_tasks, settings, db)

    return generic_response
