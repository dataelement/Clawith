# Critical Security Vulnerability: Username Collision Leading to Identity Confusion and Privilege Escalation

## Summary

A severe identity confusion vulnerability exists in the user registration system that allows an attacker to gain access to another user's account through username collision. This can lead to unauthorized account access, privilege escalation, and cross-tenant data exposure.

## Severity

**Critical** - CVSS Score: 9.8

## Vulnerability Details

### Root Cause

The vulnerability exists in `backend/app/services/registration_service.py` in the `find_or_create_identity` function:

```python
# Lines 110-124
# Try to find by email
if email:
    res = await db.execute(select(Identity).where(Identity.email == email))
    identity = res.scalar_one_or_none()

# Try to find by phone
if not identity and phone:
    normalized_phone = re.sub(r"[\s\-\+]", "", phone)
    res = await db.execute(select(Identity).where(Identity.phone == normalized_phone))
    identity = res.scalar_one_or_none()

# Try to find by username  <-- VULNERABILITY HERE!
if not identity and username:
    res = await db.execute(select(Identity).where(Identity.username == username))
    identity = res.scalar_one_or_none()

if identity:
    return identity  # Returns existing Identity instead of creating new one!
```

**Problem**: When a new user registers, the system sequentially tries to find an existing Identity by email, phone, and **username**. If the username matches an existing Identity, that Identity is returned instead of creating a new one.

### Frontend Exacerbates the Issue

In `frontend/src/pages/Login.tsx` lines 118-119:

```javascript
username: form.login_identifier.split('@')[0],  // Auto-generates username from email prefix
```

This means:
- `admin@abc.com` → username `admin`
- `admin@xyz.com` → username `admin` (same!)

### Attack Scenario

**Scenario: Account Takeover**

**Prerequisites**:
- User `admin@abc.com` exists with username `admin` and password `Admin@123`
- Attacker knows the password

**Attack Steps**:
1. Attacker visits the registration page
2. Enters email `admin@xyz.com` (any non-existent email)
3. Enters the same password as `admin@abc.com` (`Admin@123`)
4. System finds existing Identity by username `admin`
5. Password verification passes (same password)
6. A new User record is created, linked to `admin@abc.com`'s Identity
7. **Attacker receives a valid token, but viewing profile shows `admin@abc.com`'s email!**

**Result**:
- Attacker can operate as `admin@abc.com`
- If `admin@abc.com` is a platform admin, attacker gains admin privileges
- Cross-tenant data access becomes possible

## Affected Code

| File | Lines | Issue |
|------|-------|-------|
| `backend/app/services/registration_service.py` | 110-134 | `find_or_create_identity` finds existing Identity by username |
| `backend/app/api/auth.py` | 173-179, 344-349 | Calls vulnerable function |
| `frontend/src/pages/Login.tsx` | 118-119 | Auto-generates username from email prefix |
| `backend/app/api/auth.py` | 181-186 | Incomplete password verification logic |

## Impact

- **Authentication Bypass**: Attackers can access other users' accounts
- **Privilege Escalation**: Attackers may gain admin privileges if the compromised Identity has them
- **Cross-Tenant Data Access**: Multiple User records linked to same Identity can cause data leakage
- **Audit Log Corruption**: Actions attributed to wrong user

## Proof of Concept

1. Deploy a fresh instance
2. Register user `admin@abc.com` with password `Test@123`
3. Attempt to register `admin@xyz.com` with the same password `Test@123`
4. Observe that registration succeeds
5. Check user profile - it shows `admin@abc.com` instead of `admin@xyz.com`

## Recommended Fix

### Option 1: Remove Username Lookup (Recommended)

Modify `backend/app/services/registration_service.py`:

```python
async def find_or_create_identity(
    self,
    db: AsyncSession,
    email: str | None = None,
    phone: str | None = None,
    username: str | None = None,
    password: str | None = None,
    is_platform_admin: bool = False,
) -> Identity:
    """Find an existing identity or create a new one."""
    identity = None

    # Only find by email and phone, NOT by username
    if email:
        res = await db.execute(select(Identity).where(Identity.email == email))
        identity = res.scalar_one_or_none()

    if not identity and phone:
        normalized_phone = re.sub(r"[\s\-\+]", "", phone)
        res = await db.execute(select(Identity).where(Identity.phone == normalized_phone))
        identity = res.scalar_one_or_none()

    # REMOVE username lookup logic

    if identity:
        # Verify password...
        return identity

    # Check username uniqueness before creating new Identity
    if username:
        existing = await db.execute(select(Identity).where(Identity.username == username))
        if existing.scalar_one_or_none():
            raise ValueError("Username already taken")

    # Create new Identity...
```

### Option 2: Enforce Username Uniqueness Check Before Registration

In `backend/app/api/auth.py`:

```python
# Add before calling find_or_create_identity
if data.username:
    existing = await db.execute(select(Identity).where(Identity.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken"
        )
```

### Option 3: Generate Unique Username in Frontend

Modify `frontend/src/pages/Login.tsx`:

```javascript
// Generate unique username to avoid collision
const baseUsername = form.login_identifier.split('@')[0];
const uniqueSuffix = Date.now().toString(36).slice(-4);
const username = `${baseUsername}_${uniqueSuffix}`;
```

## Mitigation Steps

1. **Immediate**: Add username uniqueness check before registration
2. **Short-term**: Disable username lookup in `find_or_create_identity`
3. **Long-term**: Refactor registration flow to ensure atomic and unique Identity creation

## Environment

- Version: Current main branch (as of 2026-04-03)
- Platform: All platforms affected
