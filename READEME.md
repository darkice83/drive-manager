# 🔌 SAS Drive Rig Manager

A web-based management dashboard built with FastAPI and Tailwind CSS for monitoring SMART health metrics, formatting 520B/DIP-protected drives, and performing secure zero-fill wipes on connected SAS and SATA drives.

---

## 🚀 Fresh System Deployment

To deploy this application on a fresh system (e.g., Ubuntu/Debian), run the following commands:

```bash
# 1. Clone your repository directly into /opt/drive-manager
sudo git clone https://github.com/darkice83/drive-manager.git /opt/drive-manager
cd /opt/drive-manager

# 2. Run the automated installer script
sudo ./install.sh
```

The installer will automatically:
- Install required system packages (`smartmontools`, `util-linux`, `sg3-utils`, `sqlite3`, `nginx`, `certbot`).
- Create and configure a Python virtual environment.
- Install all required Python dependencies.
- Register and start the background FastAPI service via `systemd`.

---

## 🌐 Nginx Reverse Proxy & SSL Setup

To expose the dashboard securely via a domain name with HTTPS (Let's Encrypt), follow these steps:

### 1. Configure Nginx Site
Create an Nginx configuration file at `/etc/nginx/sites-available/drive-manager`:

```nginx
server {
    server_name subdomain.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Disable buffering for real-time connection stability
        proxy_buffering off;
        proxy_read_timeout 86400;
    }
}
```

### 2. Enable Site and Test Nginx
```bash
sudo ln -s /etc/nginx/sites-available/drive-manager /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 3. Generate SSL Certificate with Certbot
```bash
sudo certbot --nginx -d subdomain.yourdomain.com
```
Certbot will automatically configure HTTPS redirects and SSL certificates.

---

## 🛠️ Management & Maintenance

- **Restart service:** `sudo systemctl restart drive-manager`
- **View service logs:** `sudo journalctl -u drive-manager -f`
- **Check database:** `sudo sqlite3 /opt/drive-manager/drive_manager.db`
