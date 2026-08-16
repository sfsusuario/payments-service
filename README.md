# payments-service

Servicio central de pagos Wompi multi-cuenta y multi-app. Cada **cuenta Wompi**
(con sus llaves de sandbox y producción) y cada **app** (API key, URL de
webhook, prefijo de referencia, cuenta asignada) se administran desde el panel
— añadir una app o una cuenta nueva no requiere tocar código.

```
Wompi (N cuentas) ──▶ api.pagos.fierro.dev/wompi/webhook   (este servicio)
                         │  1. la events key que verifica la firma identifica la cuenta
                         │  2. entre las apps de esa cuenta, enruta por prefijo:
                         ├─ ref "WB-*"          ──▶ website-seller  (api.builder.fierro.dev)
                         └─ sin prefijo (catch-all) ─▶ Sastreria-App (api.sastreria.fierro.dev)
```

- El body del evento se reenvía **crudo** (bytes originales) con el header
  `X-Forward-Key: <API key de la app>`; la app valida ese header (ya no verifica
  la firma Wompi — este servicio la verifica antes de reenviar).
- Eventos sin referencia parseable se reenvían a **ambas** apps (sus webhooks
  responden 200 e ignoran referencias que no reconocen).
- Si el destino falla, el servicio responde **502** para que Wompi reintente.
- Cada webhook queda registrado en un log visible desde el panel.

## API interna (toda la lógica Wompi centralizada)

Las apps **no guardan llaves Wompi**: solo `PAYMENTS_SERVICE_URL` y
`PAYMENTS_SERVICE_API_KEY` en su `.env`, y piden todo a este servicio con el
header `X-Api-Key` (una key por app, generada automáticamente y visible en el
panel):

| Método | Ruta                       | Uso                                                  |
| ------ | -------------------------- | ---------------------------------------------------- |
| POST   | `/api/checkout-urls`       | URL de Web Checkout firmada (website-seller)         |
| POST   | `/api/payment-links`       | Payment link single-use (Sastreria-App)              |
| POST   | `/api/nequi-transactions`  | Transacción Nequi push (Sastreria-App)               |
| GET    | `/api/transactions/{id}`   | Consulta de transacción (polling de respaldo)        |
| GET    | `/api/keys-status`         | Qué ambientes tienen llaves configuradas + pub key   |

Errores: 401 API key inválida · 503 llaves sin configurar en el panel ·
502 `{detail: {wompi: ...}}` cuando Wompi rechaza la operación.

## Panel de administración

`https://api.pagos.fierro.dev/admin` — usuario por defecto **admin / 1234**
(el panel avisa hasta que la cambies). Desde ahí se configura:

- **Cuentas Wompi**: crear/editar/eliminar cuentas, cada una con sus llaves de
  sandbox y producción (pub / private / integrity / events). La events key
  verifica la firma de cada webhook e identifica a qué cuenta pertenece.
- **Apps**: crear/editar/eliminar apps; cada una tiene su API key (regenerable),
  URL de webhook, prefijo de referencia para el enrutamiento (vacío = catch-all)
  y la cuenta Wompi que usa.
- El **log de los últimos eventos** recibidos (referencia, destino, resultado).
- **Auditoría** (`/admin/audit`): cada cambio en cuentas, apps y credenciales,
  cada login (exitoso o fallido) y cada operación de pago de la API, con fecha,
  actor, acción e **IP de origen** (respetando `X-Forwarded-For` tras nginx).
  Los valores de las llaves nunca se registran — solo qué campos cambiaron.
- Cambio de contraseña.

En el primer arranque, el seed migra la configuración del esquema anterior si
existe (crea la cuenta «principal» con las llaves guardadas y las apps
`website-seller`/`sastreria` conservando sus API keys); si no, crea la cuenta
vacía y las dos apps con keys nuevas.

Además, en **Transacciones** (`/admin/transactions`) se ven todas las
transacciones que han pasado por el dispatcher: referencia, app destino, estado
(APPROVED/DECLINED/…), monto, método de pago, cliente y ambiente, con resumen
general y filtros por app y estado.

Todo se guarda en SQLite (`payments.db`); el `.env` solo necesita `SECRET_KEY`.

## Desarrollo local (Windows)

```powershell
.\start-service.ps1
# http://localhost:8010/admin  (admin / 1234)
```

## Servidor (Ubuntu/Debian)

```bash
cd /var/www/sfs/payments-service
sudo bash install.sh      # Python 3.11+, venv, deps, .env, logs/
sudo bash serve.sh start  # gunicorn daemon en 127.0.0.1:8010
bash serve.sh debug       # o en primer plano para depurar
bash deploy.sh            # actualizar: git pull + deps + restart
```

nginx: ver `deploy/nginx.conf.example` (proxy de `api.pagos.fierro.dev` → `:8010`),
luego `sudo certbot --nginx -d api.pagos.fierro.dev`.

## Puesta en marcha completa

1. Desplegar este servicio y entrar al panel (`/admin`, admin / 1234 → cambiar contraseña).
2. Configurar las llaves de cada cuenta Wompi y, en cada app, su URL de webhook,
   prefijo y cuenta asignada.
3. Copiar la API key de cada app (tarjeta de la app) a su `.env` como
   `PAYMENTS_SERVICE_API_KEY`, junto con `PAYMENTS_SERVICE_URL`.
4. En el panel de Wompi de CADA cuenta (sandbox y producción), registrar la
   misma URL de eventos: `https://api.pagos.fierro.dev/wompi/webhook`.
5. Hacer un pago de prueba en sandbox desde cada app y revisar el log de eventos
   y `/admin/transactions`.

Para añadir una app nueva: crear la app en el panel, asignarle cuenta, URL y
prefijo, y poner su API key en el `.env` de esa app. Para añadir otra cuenta
Wompi: crearla, cargar sus llaves y asignarla a las apps que correspondan.

## Endpoints

| Método | Ruta               | Descripción                                    |
| ------ | ------------------ | ---------------------------------------------- |
| POST   | `/wompi/webhook`   | Recibe eventos Wompi y los reenvía (público)   |
| GET    | `/health`          | Healthcheck                                    |
| GET    | `/admin`           | Panel de configuración (requiere sesión)       |
| GET    | `/admin/transactions` | Transacciones registradas (requiere sesión) |
| GET    | `/admin/audit`     | Registro de auditoría (requiere sesión)        |
| POST   | `/admin/login`     | Iniciar sesión                                 |
| POST   | `/admin/settings`  | Guardar llaves y URLs                          |
| POST   | `/admin/password`  | Cambiar contraseña                             |
