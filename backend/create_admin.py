from getpass import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_engine
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.security.passwords import hash_password


email = input("Admin email: ").strip().lower()
name = input("Admin name: ").strip() or "PhotoFlow Admin"
password = getpass("Admin password: ")

db = Session(get_engine())

try:
    existing = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()

    if existing:
        print("An account with this email already exists.")
    else:
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )

        db.add(user)
        db.commit()

        print("ADMIN CREATED")
        print("Email:", email)
        print("Role:", user.role.value)

finally:
    db.close()
