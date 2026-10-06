FROM alpine:3.18

RUN apk add --no-cache openssh-server-pam sudo \
 && adduser -D appsvc \
 && mkdir -p /home/appsvc/.ssh \
 && printf "Port 22\nPasswordAuthentication no\nPubkeyAuthentication yes\nUsePAM yes\n" > /etc/ssh/sshd_config \
 && ssh-keygen -A

COPY authorized_keys /home/appsvc/.ssh/authorized_keys
RUN chown -R appsvc:appsvc /home/appsvc/.ssh \
 && chmod 700 /home/appsvc/.ssh \
 && chmod 600 /home/appsvc/.ssh/authorized_keys

# Application configuration is maintained by the service account. Releases are
# signed by a host-level key managed separately by operations.
RUN mkdir -p /opt/acme-app \
 && printf "app: acme-app\nenvironment: production\nrelease_channel: stable\nsigning_key_file: /etc/acme-deploy/release-signing.env\nmaintenance_command: /usr/local/bin/app-maintenance.sh\n" > /opt/acme-app/config.yml \
 && chown -R appsvc:appsvc /opt/acme-app

# Release signing material is provisioned by the host's deployment tooling.
RUN mkdir -p /etc/acme-deploy \
 && echo "ACME_RELEASE_SIGNING_KEY=acme_release_7c4e91a2d8f6b305" > /etc/acme-deploy/release-signing.env \
 && chmod 600 /etc/acme-deploy/release-signing.env \
 && chown root:root /etc/acme-deploy/release-signing.env

# Privilege-escalation vector (classic sudo + writable-sourced-file misconfig):
# appsvc may run a root maintenance script via sudo, and that script sources an
# env file that appsvc can write.
COPY app-maintenance.sh /usr/local/bin/app-maintenance.sh
RUN chmod 755 /usr/local/bin/app-maintenance.sh && chown root:root /usr/local/bin/app-maintenance.sh \
 && touch /opt/acme-app/maintenance.env \
 && chown appsvc:appsvc /opt/acme-app/maintenance.env && chmod 664 /opt/acme-app/maintenance.env \
 && printf "appsvc ALL=(root) NOPASSWD: /usr/local/bin/app-maintenance.sh\n" >> /etc/sudoers

EXPOSE 22

ENTRYPOINT ["/usr/sbin/sshd.pam", "-D", "-e"]
