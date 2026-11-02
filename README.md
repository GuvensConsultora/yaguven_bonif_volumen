# Yagüven Bonificación por Volumen

Módulo Odoo 19 que bonifica a los clientes según lo que compran en el **mes calendario**, sin que
quien factura tenga que ir a sumar al mayor del cliente.

## ¿Qué problema resuelve?

Muchas empresas premian el volumen: si el cliente supera cierto monto en el mes, le bonifican un
porcentaje sobre todo lo comprado. Al facturar, nadie sabe cuánto lleva el cliente en el mes, y el
cruce del tope se detecta tarde o a mano.

Con el módulo, **al confirmar la factura** Odoo hace la cuenta y, si corresponde bonificar, lo
muestra en un asistente antes de confirmar.

## Cómo funciona

Ejemplo: 4% desde $5.000.000 y 5% desde $10.000.000.

| Día | Factura | Acumulado | Qué pasa |
|---|---|---|---|
| 5 | $1.000.000 | $1.000.000 | Se confirma normal. |
| 15 | $2.000.000 | $3.000.000 | Se confirma normal. |
| 27 | $3.000.000 | $6.000.000 | Asistente: 4% en esta factura (línea de descuento) + NC en borrador por el 4% de las del 5 y 15. |
| 28 | $5.000.000 | $11.000.000 | Asistente: 5% en esta factura + NC en borrador por el 1% de diferencia de las del 5, 15 y 27. |

- La **NC lleva una línea por factura bonificada** («Bonificación por volumen 4% s/ FA-A …»).
- Cada factura sabe qué % ya lleva bonificado (sus líneas + NC no canceladas), así que nunca se
  bonifica dos veces y al subir de tramo sólo se aplica la diferencia.
- Las NC de devolución restan del acumulado; las de bonificación no.
- El asistente permite **confirmar sin bonificación**, y lo deja asentado en el historial de la factura.
- Si se confirman facturas en forma masiva y alguna genera bonificación, se frena con un aviso.

## Configuración

1. *Ajustes › Empresas › (empresa) › solapa Bonificación por volumen*:
   - **Producto de bonificación** (servicio): de él salen la cuenta contable y los impuestos de las líneas.
   - **Monto que se compara**: neto sin impuestos o total con impuestos.
   - **Contar sólo facturas cobradas**: las facturas anteriores suman y reciben la NC sólo si están cobradas.
   - **Escala general**: tramos «desde monto → %», sin límite de niveles.
2. *Contacto › solapa Bonificación por volumen*: escala particular del cliente, que **reemplaza** a la general.
3. Todas las escalas juntas: *Facturación › Configuración › Bonificación por volumen*.

Sin escala cargada, el módulo no interviene.

## Arquitectura

- `yaguven.volume.discount.tier` — tramos, por empresa y opcionalmente por cliente.
- `account.move` — se hereda `action_post` (abre el asistente, igual que el asistente nativo de
  montos anormales) y `_post` (red de seguridad para confirmaciones masivas).
- `account.move.line` — dos campos: factura bonificada y % aplicado.
- Depende sólo de `account`. No reemplaza vistas ni acciones del core.

## Verificación

- Cargar una escala, facturar al mismo cliente por debajo del tope y confirmar: confirma normal.
- Facturar hasta cruzar el tope: aparece el asistente con las ventas del mes; al aplicar, la factura
  queda con la línea de descuento y se abre la NC en borrador con una línea por factura anterior.
- Tests: `odoo-bin -i yaguven_bonif_volumen --test-tags /yaguven_bonif_volumen`.

## Licencia

LGPL-3 — Yagüven C.G.
