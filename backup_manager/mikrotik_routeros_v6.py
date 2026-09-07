"""RouterOS v6 template owned by Backup Manager Local."""

HEADER = "# Backup Manager Local - FTP Push - RouterOS v6"
COMPATIBILITY = "RouterOS 6.43 ou mais recente"


def export_command(base_variable: str) -> str:
    return f"/export compact file=${base_variable}"


def upload_command(local_variable: str, remote_variable: str) -> str:
    return (
        "/tool fetch mode=ftp upload=yes address=$bmHost port=$bmPort "
        "user=$bmUser password=$bmPassword src-path=$"
        f"{local_variable} dst-path=${remote_variable} keep-result=no"
    )
