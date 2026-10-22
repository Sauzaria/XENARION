from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


class LicenseKey(db.Model):
    __tablename__ = "license_keys"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    tier = db.Column(db.Integer, default=1)          # 0=Basic 1=Pro 2=Ultimate
    hwid = db.Column(db.String(128), nullable=True)   # NULL = 全PC有効
    expires_at = db.Column(db.DateTime, nullable=True)  # NULL = 無期限
    revoked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = db.Column(db.DateTime, nullable=True)
    activation_count = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            "key": self.key,
            "tier": self.tier,
            "hwid_bound": self.hwid is not None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked": self.revoked,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }