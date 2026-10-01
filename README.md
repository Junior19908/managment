# Visualizador de Serviços OData (CATALOGSERVICE)

Aplicação desktop (Tkinter) para explorar os serviços OData registrados no
SAP Gateway (`/IWFND/CATALOGSERVICE;v=2`) e inspecionar o `$metadata` de cada
serviço — EntitySets, EntityTypes, propriedades (com chaves e `sap:label`) e
FunctionImports.

## Requisitos

- Python 3.9+ (testado com 3.13) com Tkinter
- `requests` (apenas para o modo online / download de `$metadata`)

```bash
pip install -r requirements.txt
python app.py
```

## Como usar

1. **Primeira execução**: defina uma senha local para o aplicativo. A sessão
   fica válida por 24 h; depois disso a senha é pedida novamente.
2. **Fonte de dados**
   - *Local*: lê um `metadata.json` (o JSON do `ServiceCollection`). Caminhos
     relativos são resolvidos a partir da pasta do `app.py`.
   - *Online*: consulta o CATALOGSERVICE com usuário/senha SAP. Depois de
     carregar, use **Salvar lista como JSON** para gerar um `metadata.json`
     e trabalhar offline.
3. **Aba Serviços OData**: filtre (Ctrl+F), ordene clicando no cabeçalho,
   copie a URL, abra no navegador ou dê duplo clique para baixar o `$metadata`.
4. **Aba $metadata**: carregue online (Ctrl+M) ou de um arquivo XML/EDMX
   (Ctrl+O). Clicar em um EntitySet seleciona o EntityType e lista as
   propriedades (🔑 = chave; coluna *C U D* = creatable/updatable/deletable).
   Busque no XML, formate-o ou salve-o em disco.

### Atalhos

| Atalho | Ação |
| --- | --- |
| F5 | Carregar serviços |
| Ctrl+F | Focar o filtro / busca no XML |
| Ctrl+E | Exportar lista (CSV) |
| Ctrl+M | Carregar `$metadata` online do serviço selecionado |
| Ctrl+O | Carregar `$metadata` de arquivo |
| Duplo clique | Abrir `$metadata` do serviço |

## Arquivos gerados (ignorados pelo git)

- `catalog_viewer_config.json` — URL, usuário SAP, hash da senha do app
  (PBKDF2-SHA256 com salt), sessão, último serviço, geometria da janela.
  A senha SAP **nunca** é gravada.
- `catalog_viewer.log` — log rotativo (1 MB × 3).

Esqueceu a senha do aplicativo? Remova a chave `app_password_hash` do
`catalog_viewer_config.json` e uma nova senha será pedida.

## Estrutura

```
app.py                       # ponto de entrada
catalog_viewer/
  config.py                  # AppConfig (load/save atômico, caminhos)
  security.py                # hash de senha + sessão
  services.py                # normalização, filtro, URLs, CSV
  odata_client.py            # HTTP (requests) com erros amigáveis
  metadata_parser.py         # EDMX -> MetadataModel, pretty-print
  logging_setup.py           # log rotativo
  ui/app.py                  # janela principal (threads p/ rede)
  ui/dialogs.py              # senha / login
  ui/widgets.py              # trees com scroll+ordenação, text read-only…
tests/                       # pytest (lógica pura, sem Tk)
```

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest
```
