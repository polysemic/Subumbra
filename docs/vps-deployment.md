# VPS Deployment Guide

*General Ubuntu 24.04 VPS baseline for a typical web server before any
application-specific deployment.*

This guide is intentionally **provider-agnostic** and **app-agnostic**. It
prepares a fresh Ubuntu 24.04 VPS the way many operators already do for a
normal hosted site:

- SSH keys
- non-root sudo user
- basic SSH hardening
- `ufw`
- optional `fail2ban`
- UTC timekeeping
- `nginx`
- Let's Encrypt
- `mariadb`
- standard host utilities

Use this as the clean host baseline. Add Docker, Subumbra, and any
Cloudflare Tunnel setup afterward in a separate deployment guide.

---

## 1. Recommended Baseline

For a general-purpose VPS:

- `Ubuntu 24.04 LTS`
- `2-4 vCPU`
- `4-8 GB RAM`
- `60+ GB SSD/NVMe`

This is a reasonable starting point for:

- a normal web server
- reverse proxy + TLS
- a small database
- containerized apps later

---

## 2. Initial Access

Most VPS providers give you:

- a public IP
- a root password or console login
- optional cloud-init user data

Log in once as `root`, then move to key-based access immediately.

### Generate an SSH key locally

On your workstation:

```bash
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/ssh-key-yourserver.key -C "yourserver"
```

This creates:

- private key: `~/.ssh/ssh-key-yourserver.key`
- public key: `~/.ssh/ssh-key-yourserver.key.pub`

### Optional local SSH config

Add a host entry to `~/.ssh/config`:

```sshconfig
Host yourserver
    HostName your.server.ip.or.domain
    User root
    IdentityFile ~/.ssh/ssh-key-yourserver.key
    IdentitiesOnly yes
```

### Install the public key on the server

If root password login is still enabled:

```bash
ssh-copy-id -i ~/.ssh/ssh-key-yourserver.key.pub root@your.server.ip
```

If `ssh-copy-id` is not available, paste the public key manually into:

```text
/root/.ssh/authorized_keys
```

Then fix permissions:

```bash
mkdir -p /root/.ssh
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
```

Open a **second terminal** and confirm key login works before changing SSH
settings:

```bash
ssh yourserver
```

---

## 3. Create A Normal Sudo User

Create a non-root administrative user:

```bash
adduser deploy
usermod -aG sudo deploy
```

Copy SSH access to that user:

```bash
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

Update your local SSH config:

```sshconfig
Host yourserver
    HostName your.server.ip.or.domain
    User deploy
    IdentityFile ~/.ssh/ssh-key-yourserver.key
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

Test:

```bash
ssh yourserver
sudo whoami
```

Expected result:

```text
root
```

Do not continue until this works from a second terminal.

---

## 4. Update The Server

Log in as your new sudo user and update the base system:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt autoremove -y
```

Install common host utilities:

```bash
sudo apt install -y \
  curl \
  wget \
  git \
  unzip \
  zip \
  ca-certificates \
  gnupg \
  lsb-release \
  software-properties-common \
  apt-transport-https \
  jq \
  htop \
  tree \
  net-tools \
  fail2ban \
  ufw
```

---

## 5. Set UTC Timekeeping

Use UTC on servers unless you have a very specific reason not to.

Why:

- logs line up across services and hosts
- cron/systemd timers are easier to reason about
- API timestamps stay consistent
- hosted/multi-region systems behave more predictably

Set timezone to UTC:

```bash
sudo timedatectl set-timezone UTC
timedatectl status
```

Make sure time sync is active:

```bash
timedatectl
systemctl status systemd-timesyncd
```

Best practice:

- store timestamps in UTC
- keep server timezone in UTC
- convert to local time only in the UI if needed

---

## 6. Harden SSH

Edit:

```text
/etc/ssh/sshd_config
```

Recommended baseline:

```text
PubkeyAuthentication yes
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
UsePAM yes
X11Forwarding no
PermitEmptyPasswords no
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
```

Notes:

- On Ubuntu 24.04, `KbdInteractiveAuthentication no` is the main modern
  setting you will usually see. Older guides often mention
  `ChallengeResponseAuthentication no`; that directive may be absent on newer
  systems and usually does not need to be added separately.
- `ClientAliveInterval` and `ClientAliveCountMax` help reduce idle SSH sessions
  being dropped by intermediate network timeouts.

Validate config:

```bash
sudo sshd -t
```

Reload SSH:

```bash
sudo systemctl reload ssh
```

Keep your current session open and test login again from a second terminal.

---

## 7. Configure UFW

Even if the server will later use Cloudflare Tunnel, `ufw` is still worth
enabling. It adds very little overhead and protects you if a service is
accidentally exposed later.

### Minimum safe baseline

Allow SSH first:

```bash
sudo ufw allow OpenSSH
```

If this VPS will host a normal public website, also allow:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

Enable the firewall:

```bash
sudo ufw enable
sudo ufw status verbose
```

Notes:

- `ufw status verbose` commonly shows separate IPv4 and IPv6 rules. Entries
  marked `Anywhere` are IPv4, and entries marked `Anywhere (v6)` are IPv6.
  That is normal and does not mean the same rule was added twice incorrectly.
- If you do not want IPv6 firewall rules at all, you can disable IPv6 support
  in `/etc/default/ufw` by setting `IPV6=no` before reloading UFW, but most
  operators should leave IPv6 enabled unless they are sure the host will never
  use it.

### Recommended modes

**Standard public web server**

- allow `22/tcp`
- allow `80/tcp`
- allow `443/tcp`

**Tunnel-only application host**

- allow `22/tcp` only
- do **not** allow `80/443`
- bind app services to `127.0.0.1` or private Docker networks

UFW does not block normal outbound traffic by default, so package installs,
Docker pulls, and application outbound requests still work normally.

---

## 8. Configure fail2ban

`fail2ban` is most useful when you expose SSH or other public services.

If the server has public SSH, keep it. If the server has no public inbound
services except private admin access, it becomes optional.

Create a minimal local jail config:

```bash
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local
```

Edit:

```text
/etc/fail2ban/jail.local
```

At minimum, confirm the `sshd` jail is enabled:

```ini
[sshd]
enabled = true
port    = ssh
logpath = %(sshd_log)s
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
```

Notes:

- On Ubuntu 24.04, `backend = systemd` is usually the best explicit choice for
  `sshd` because SSH authentication events are typically available through
  `systemd-journald`.
- Some generic Fail2ban examples use `backend = %(sshd_backend)s` so the distro
  default decides. That is more portable across different Linux distributions,
  but this guide is Ubuntu-specific, so the explicit `systemd` backend is
  clearer.
- When adding future jails for other services, do not assume they should all
  reuse the SSH settings. Check whether each service logs to journald or to a
  plain file, then set `backend` and `logpath` accordingly.

Restart and verify:

```bash
sudo systemctl enable fail2ban
sudo systemctl restart fail2ban
sudo fail2ban-client status
sudo fail2ban-client status sshd
```

---

## 9. Install nginx

Install nginx:

```bash
sudo apt install -y nginx
```

Enable and start it:

```bash
sudo systemctl enable nginx
sudo systemctl start nginx
sudo systemctl status nginx
```

If `ufw` allows `80/443`, you should now be able to reach:

```text
http://your-domain-or-ip
```

At this point, Ubuntu’s default nginx page should usually load. That confirms:

- nginx is installed
- nginx is listening on port `80`
- the firewall is allowing web traffic
- DNS is working if you tested with your domain name

If the server IP works but the domain does not:

- confirm the domain’s `A` record points to the VPS
- confirm any `AAAA` record is also correct if you are using IPv6
- remember `www.example.com` needs its own DNS record if you want it to work

### Typical site directory layout

Common pattern:

- web root: `/var/www/yoursite`
- nginx server block: `/etc/nginx/sites-available/yoursite`
- enabled symlink: `/etc/nginx/sites-enabled/yoursite`

### Create the web root

Example:

```bash
sudo mkdir -p /var/www/yoursite
sudo chown -R $USER:$USER /var/www/yoursite
```

Create a simple test page:

```bash
cat > /var/www/yoursite/index.html <<'EOF'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Your Site</title>
</head>
<body>
  <h1>Your site is live</h1>
  <p>If you can read this, nginx is serving your site correctly.</p>
</body>
</html>
EOF
```

### Create the server block

Create:

```text
/etc/nginx/sites-available/yoursite
```

Example server block:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;

    root /var/www/yoursite;
    index index.html index.htm;

    location / {
        try_files $uri $uri/ =404;
    }
}
```

You can create it with:

```bash
sudo nano /etc/nginx/sites-available/yoursite
```

Paste the config, save, and exit.

### Enable the site

Create the symlink:

```bash
sudo ln -s /etc/nginx/sites-available/yoursite /etc/nginx/sites-enabled/
```

Optional cleanup:

Ubuntu often ships with a default site enabled. If you want only your site to
answer, remove the default symlink:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
```

### Test and reload nginx

Always test the configuration before reload:

```bash
sudo nginx -t
```

If the output says the syntax is OK, reload nginx:

```bash
sudo systemctl reload nginx
```

### Verify the site

Test locally on the server:

```bash
curl -I http://127.0.0.1
curl http://127.0.0.1
```

Test from your workstation:

```bash
curl -I http://example.com
curl -I http://www.example.com
```

Expected behavior:

- `example.com` works if its DNS record points to the server
- `www.example.com` works only if you created a `www` DNS record

### Common beginner mistakes

- forgetting to create the DNS record for `www`
- editing `sites-available` but forgetting the symlink in `sites-enabled`
- forgetting to remove the default site when expecting only the new site
- reloading nginx without running `sudo nginx -t` first
- pointing DNS to the wrong IPv4 or IPv6 address
- leaving the web root owned by `root` when you intend to edit files as your normal user

---

## 10. Install Let's Encrypt

Install Certbot and the nginx plugin:

```bash
sudo apt install -y certbot python3-certbot-nginx
```

Request a certificate:

```bash
sudo certbot --nginx -d example.com -d www.example.com
```

Test renewal:

```bash
sudo certbot renew --dry-run
```

This assumes:

- your DNS already points to the VPS
- ports `80` and `443` are reachable
- nginx is serving the correct hostname

If you later use Cloudflare Tunnel instead of direct ingress, your TLS setup may
be different and this step may not be needed for the app itself.

---

## 11. Optional: Automatic Security Updates

Install and enable unattended upgrades:

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

This is a common good baseline for small VPS deployments.

---

## 12. Optional: Install MariaDB

Install:

```bash
sudo apt install -y mariadb-server mariadb-client
```

Enable and start:

```bash
sudo systemctl enable mariadb
sudo systemctl start mariadb
sudo systemctl status mariadb
```

Run the security setup:

```bash
sudo mysql_secure_installation
```

Typical good answers:

- switch to unix socket auth for root: yes
- change root password: optional if using socket auth only
- remove anonymous users: yes
- disallow remote root login: yes
- remove test database: yes
- reload privilege tables: yes

### Create a database and user

```bash
sudo mariadb
```

Then:

```sql
CREATE DATABASE appdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'appuser'@'localhost' IDENTIFIED BY 'strong-password-here';
GRANT ALL PRIVILEGES ON appdb.* TO 'appuser'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Baseline recommendation:

- keep MariaDB bound to localhost unless remote DB access is explicitly needed

Check bind address:

```bash
sudo rg "bind-address" /etc/mysql /etc/mysql/mariadb.conf.d
```

---

## 13. Optional: Basic App Runtime Packages

Depending on what you host later, common additions are:

```bash
sudo apt install -y \
  python3 \
  python3-pip \
  python3-venv \
  nodejs \
  npm
```

For a container-first deployment, you may skip these until needed.

---

## 14. DNS And Domain Notes

Typical direct web hosting:

- `A` record -> VPS public IP
- `AAAA` record -> VPS IPv6 if used

If using Cloudflare:

- `DNS only` is usually simplest during initial server setup
- switch to proxied mode later if desired
- normal SSH does **not** work through proxied orange-cloud DNS

If using a Cloudflare Tunnel later:

- the tunnel only affects hostnames/services you explicitly map
- it does not replace the server’s normal outbound routing
- you can tunnel only one application without changing the rest of the host

---

## 15. Basic Verification Checklist

SSH:

```bash
ssh yourserver
sudo whoami
```

Firewall:

```bash
sudo ufw status verbose
```

Time:

```bash
timedatectl status
```

fail2ban:

```bash
sudo fail2ban-client status
```

nginx:

```bash
sudo systemctl status nginx
curl -I http://127.0.0.1
```

MariaDB:

```bash
sudo systemctl status mariadb
```

TLS:

```bash
sudo certbot renew --dry-run
```

---

## 16. What This Guide Does Not Cover

This baseline intentionally does **not** cover:

- Docker installation
- application containers
- Subumbra bootstrap
- Cloudflare Tunnel deployment
- reverse proxying containers through nginx
- database backup strategy
- monitoring stack setup

Those belong in application-specific deployment docs layered on top of this
host baseline.

---

## 17. Practical Snapshot

At the end of this guide, a typical VPS should have:

- key-only SSH access
- root SSH login disabled
- a normal sudo user
- UTC timekeeping
- `ufw` enabled
- optional `fail2ban` protecting SSH
- `nginx` installed and running
- Let's Encrypt ready or installed
- `mariadb` installed and secured
- a clean baseline ready for app or Docker deployment
