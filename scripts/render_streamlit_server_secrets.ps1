param(
    [Parameter(Mandatory = $true)]
    [string]$PrivateKeyPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [Parameter(Mandatory = $true)]
    [string]$SshHost,

    [int]$SshPort = 22,
    [string]$SshUsername = "root2",

    [Parameter(Mandatory = $true)]
    [string]$HostKeySha256,

    [string]$RemoteApiHost = "127.0.0.1",
    [int]$RemoteApiPort = 8000
)

$ErrorActionPreference = "Stop"
$resolvedKey = (Resolve-Path -LiteralPath $PrivateKeyPath).Path
$privateKey = [System.IO.File]::ReadAllText($resolvedKey).Trim()
if (-not $privateKey.StartsWith("-----BEGIN OPENSSH PRIVATE KEY-----")) {
    throw "PrivateKeyPath is not an OpenSSH private key: $resolvedKey"
}

$content = @"
NF_SSH_HOST = "$SshHost"
NF_SSH_PORT = $SshPort
NF_SSH_USERNAME = "$SshUsername"
NF_SSH_HOST_KEY_SHA256 = "$HostKeySha256"
NF_REMOTE_API_HOST = "$RemoteApiHost"
NF_REMOTE_API_PORT = $RemoteApiPort
NF_SSH_PRIVATE_KEY = '''
$privateKey
'''
"@

$parent = Split-Path -Parent $OutputPath
if ($parent) {
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
}
[System.IO.File]::WriteAllText($OutputPath, $content, [System.Text.UTF8Encoding]::new($false))
Write-Host "Streamlit Secrets created: $OutputPath"
Write-Host "Paste the complete file into Streamlit Cloud -> App settings -> Secrets. Never commit it."
