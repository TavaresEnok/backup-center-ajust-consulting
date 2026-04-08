import os
import pty
import select
import sys

command = "su"
args = ["su", "-c", "cd /srv/backup_center_new && chmod 644 .env && docker-compose build app celery celery_vpn && docker-compose up -d app celery celery_vpn celery_beat", "root"]

pid, fd = pty.fork()

if pid == 0:
    # Child process
    os.execlp(command, *args)
else:
    # Parent process
    password_sent = False
    while True:
        try:
            r, w, x = select.select([fd], [], [])
            if fd in r:
                out = os.read(fd, 1024)
                if not out:
                    break
                sys.stdout.buffer.write(out)
                sys.stdout.buffer.flush()
                
                # Look for the password prompt
                # Portuguese is usually "Senha:" and English "Password:"
                if not password_sent and (b'enha' in out.lower() or b'ssword' in out.lower()):
                    # Send exactly the password plus newline
                    os.write(fd, b'zS2npBNSx$C#a#am\n')
                    password_sent = True
        except OSError:
            break
