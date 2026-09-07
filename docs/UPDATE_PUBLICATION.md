# Publicação de atualizações

Gere o `.bmu` e, em uma estação de release isolada, execute:

```bash
python3 scripts/publish-update-index.py --packages /pacotes --output /publicavel \
  --base-url https://updates.example/backup-manager \
  --private-key /segredos/update-private-key.pem
```

O script revalida a assinatura interna, copia releases, cria assinaturas destacadas, gera o índice fechado e o assina. Ele não faz upload. Publique manualmente por rsync/scp em HTTPS, usando `application/json` para o índice e `application/octet-stream` para assinaturas/pacotes.

A chave privada nunca deve entrar no Git, pacote, produto ou diretório publicável. Apenas a chave pública é instalada nos clientes. Guarde backup offline e defina procedimento de rotação antes de substituí-la.
