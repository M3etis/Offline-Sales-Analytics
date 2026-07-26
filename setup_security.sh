#!/bin/bash
# Security Fixes Setup Script
# Run this script after pulling security patches

set -e

echo "🔐 Sales Analytics - Security Setup"
echo "===================================="
echo ""

# Check if .env exists
if [ -f .env ]; then
    echo "⚠️  .env file already exists"
    read -p "Do you want to regenerate SECRET_KEY? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Skipping SECRET_KEY generation"
        exit 0
    fi
fi

# Generate SECRET_KEY
echo "📝 Generating secure SECRET_KEY..."
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')

# Copy .env.example if .env doesn't exist
if [ ! -f .env ]; then
    echo "📄 Creating .env from .env.example..."
    cp .env.example .env
fi

# Update or add SECRET_KEY in .env
if grep -q "^SECRET_KEY=" .env; then
    # Replace existing SECRET_KEY
    sed -i.bak "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET_KEY|" .env
    rm .env.bak
    echo "✅ Updated SECRET_KEY in .env"
else
    # Add SECRET_KEY
    echo "SECRET_KEY=$SECRET_KEY" >> .env
    echo "✅ Added SECRET_KEY to .env"
fi

echo ""
echo "✅ Security setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Review .env file and adjust ALLOWED_ORIGINS if needed"
echo "2. Restart backend: uvicorn app.main:app --reload"
echo "3. Test login functionality"
echo ""
echo "🔍 To verify security fixes:"
echo "- Check SECURITY_FIXES.md for testing instructions"
echo "- Run: python3 -c 'from app.core.config import settings; print(\"OK\")'"
echo ""
