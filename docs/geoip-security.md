# GeoLite2 no relatório de segurança

O relatório usa bases locais MaxMind DB e nunca envia os IPs registrados para
um serviço externo.

Arquivos esperados:

- `/usr/share/GeoIP/GeoLite2-Country.mmdb`
- `/usr/share/GeoIP/GeoLite2-ASN.mmdb`

Os caminhos podem ser alterados pelas variáveis
`BACKUP_MANAGER_GEOIP_COUNTRY_DB` e `BACKUP_MANAGER_GEOIP_ASN_DB`.

As bases GeoLite2 devem ser obtidas diretamente na MaxMind com uma conta e uma
chave de licença válidas. Mantenha os arquivos atualizados e legíveis apenas
pelos serviços administrativos. A geolocalização indica uma região aproximada;
não identifica endereço residencial ou a localização exata de uma pessoa.
