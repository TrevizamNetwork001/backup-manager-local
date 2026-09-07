# Formato do pacote de atualização

No repositório remoto, o pacote também possui `.bmu.sig`, que assina o SHA-256 hexadecimal ASCII. Essa camada protege o transporte antes da assinatura interna descrita abaixo.

O `.bmu` é um `tar.gz` fechado, sem comandos livres. Ele contém exatamente `manifest.json`, `checksums.txt`, `signature` e arquivos declarados sob `payload/`, `migrations/` e `units/`.

O manifesto possui `product`, `version`, limites de versão, canal, build, data, hash agregado do payload, espaço requerido, indicação e allowlist de reinícios, migrations, arquivos (`path`, `sha256`, `size`, `action`), checks pré/pós e notas. Campos desconhecidos invalidam o pacote.

`signature` é uma assinatura Ed25519 do JSON canônico do manifesto, uma quebra de linha e os bytes de `checksums.txt`. Cada conteúdo é conferido contra tamanho e SHA-256; o hash agregado do payload concatena, em ordem de path, `path + NUL + sha256 + LF`.

Arquivos absolutos, `..`, barras invertidas, links, devices, duplicatas, conteúdo não declarado e limites excedidos são recusados antes da extração. Não existe fallback sem assinatura.

O build é feito com `scripts/build-update-package.py --version ... --minimum-version ... --private-key /caminho/externo`. A chave privada nunca fica no repositório ou pacote. Para testes, gere uma chave efêmera somente em `/tmp`.
