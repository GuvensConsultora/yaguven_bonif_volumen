from collections import defaultdict

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError


class VolumeDiscountWizard(models.TransientModel):
    _name = "yaguven.volume.discount.wizard"
    _description = "Asistente de bonificación por volumen"

    move_id = fields.Many2one("account.move", string="Factura", required=True, readonly=True)
    partner_id = fields.Many2one(related="move_id.commercial_partner_id", string="Cliente")
    currency_id = fields.Many2one(related="move_id.company_id.currency_id")
    period = fields.Char(string="Mes", readonly=True)
    base = fields.Selection(related="move_id.company_id.vd_base", string="Se compara")
    accumulated_before = fields.Monetary(string="Acumulado antes de esta factura", readonly=True)
    own_volume = fields.Monetary(string="Esta factura", readonly=True)
    total = fields.Monetary(string="Acumulado del mes", readonly=True)
    previous_percent = fields.Float(string="% que regía", digits=(5, 2), readonly=True)
    percent = fields.Float(string="% alcanzado", digits=(5, 2), readonly=True)
    scale_text = fields.Text(string="Escala aplicada", readonly=True)
    explanation = fields.Html(string="Por qué", readonly=True, sanitize=False)
    line_ids = fields.One2many(
        "yaguven.volume.discount.wizard.line", "wizard_id", string="Ventas del mes", readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        move = self.env["account.move"].browse(res.get("move_id") or self.env.context.get("default_move_id"))
        if not move:
            return res
        data = move._vd_compute()
        if not data:
            raise UserError(self.env._("Esta factura no tiene bonificación por volumen para aplicar."))
        res.update(self._values_from(move, data))
        return res

    def _values_from(self, move, data):
        currency = move.company_id.currency_id
        fmt = lambda amount: currency.format(amount)  # noqa: E731
        date_from, _ = move._vd_period()
        pending = {p["move"].id: p["percent"] for p in data["pending"]}
        scope = data["scale"][:1].partner_id and self.env._("escala particular del cliente") \
            or self.env._("escala general de la empresa")
        scale_text = "\n".join(
            self.env._("Desde %(amount)s → %(pct)s%%", amount=fmt(t.amount_from), pct=f"{t.percent:g}")
            for t in data["scale"]
        )

        lines = []
        for h in data["history"]:
            m = h["move"]
            note = "" if h["counts"] else self.env._("No suma: factura sin cobrar")
            lines.append(fields.Command.create({
                "move_id": m.id,
                "volume": h["volume"],
                "applied_percent": m._vd_applied_percent() if m.move_type == "out_invoice" else 0.0,
                "pending_percent": pending.get(m.id, 0.0),
                "counts": h["counts"],
                "note": note,
            }))
        lines.append(fields.Command.create({
            "move_id": move.id,
            "is_current": True,
            "volume": data["own_volume"],
            "applied_percent": move._vd_applied_percent(),
            "pending_percent": pending.get(move.id, 0.0),
            "counts": True,
            "note": self.env._("Factura que se está confirmando"),
        }))

        previous = [p for p in data["pending"] if p["move"] != move]
        parts = [
            self.env._(
                "El cliente lleva facturado en %(month)s <b>%(total)s</b> (%(before)s antes de esta "
                "factura + %(own)s de esta). Según la %(scope)s le corresponde <b>%(pct)s%%</b>.",
                month=escape(date_from.strftime("%m/%Y")), total=escape(fmt(data["total"])),
                before=escape(fmt(data["accumulated_before"])), own=escape(fmt(data["own_volume"])),
                scope=escape(scope), pct=f"{data['percent']:g}",
            ),
        ]
        if move.id in pending:
            parts.append(self.env._(
                "En esta factura se agrega la línea de bonificación del %(pct)s%%.",
                pct=f"{pending[move.id]:g}",
            ))
        if previous:
            parts.append(self.env._(
                "Por las %(n)s facturas anteriores del mes se genera una nota de crédito en "
                "borrador, con una línea por factura y el %% que le falta a cada una.",
                n=len(previous),
            ))
        return {
            "move_id": move.id,
            "period": date_from.strftime("%m/%Y"),
            "accumulated_before": data["accumulated_before"],
            "own_volume": data["own_volume"],
            "total": data["total"],
            "previous_percent": data["previous_percent"],
            "percent": data["percent"],
            "scale_text": scale_text,
            "explanation": Markup("<p>%s</p>") % Markup("</p><p>").join(Markup(p) for p in parts),
            "line_ids": lines,
        }

    # ----------------------------------------------------------------- acciones

    def action_apply(self):
        self.ensure_one()
        move = self.move_id
        if move.state != "draft":
            raise UserError(self.env._("La factura ya no está en borrador."))
        # Se recalcula con el estado actual: entre que se abrió el asistente y ahora pudo
        # confirmarse o cancelarse otra factura del cliente.
        data = move._vd_compute()
        pending = (data or {}).get("pending") or []

        own = next((p for p in pending if p["move"] == move), None)
        if own:
            vals = move._vd_discount_vals(own["percent"])
            for v in vals:
                v["price_unit"] = -v["price_unit"]
            move.write({"invoice_line_ids": [fields.Command.create(v) for v in vals]})

        refunds = self._create_refunds(move, [p for p in pending if p["move"] != move])
        move.with_context(vd_checked=True).action_post()

        if refunds:
            links = Markup(", ").join(r._get_html_link() for r in refunds)
            move.message_post(body=Markup(self.env._(
                "Bonificación por volumen del %(pct)s%%: nota de crédito en borrador %(links)s "
                "por las facturas anteriores del mes.", pct=f"{data['percent']:g}", links=links,
            )))
            return {
                "type": "ir.actions.act_window",
                "res_model": "account.move",
                "view_mode": "form" if len(refunds) == 1 else "list,form",
                "res_id": refunds.id if len(refunds) == 1 else False,
                "domain": [("id", "in", refunds.ids)],
            }
        return {"type": "ir.actions.act_window_close"}

    def _create_refunds(self, move, previous):
        """Una NC en borrador por moneda, con una línea por factura anterior y grupo de IVA."""
        by_currency = defaultdict(list)
        for p in previous:
            by_currency[p["move"].currency_id].append(p)
        refunds = self.env["account.move"]
        for currency, items in by_currency.items():
            lines = []
            for p in items:
                lines += [fields.Command.create(v) for v in p["move"]._vd_discount_vals(p["percent"])]
            refunds |= self.env["account.move"].create({
                "move_type": "out_refund",
                "company_id": move.company_id.id,
                "partner_id": move.commercial_partner_id.id,
                "currency_id": currency.id,
                "journal_id": move.journal_id.id,
                "invoice_date": move.invoice_date or fields.Date.context_today(self),
                "invoice_origin": ", ".join(p["move"].name for p in items),
                "ref": self.env._("Bonificación por volumen %(period)s", period=self.period),
                "invoice_line_ids": lines,
            })
        return refunds

    def action_post_without_discount(self):
        self.ensure_one()
        self.move_id.with_context(vd_checked=True).action_post()
        self.move_id.message_post(body=self.env._(
            "Confirmada sin bonificación por volumen. Correspondía %(pct)s%% sobre un acumulado "
            "del mes de %(total)s.",
            pct=f"{self.percent:g}", total=self.currency_id.format(self.total),
        ))
        return {"type": "ir.actions.act_window_close"}


class VolumeDiscountWizardLine(models.TransientModel):
    _name = "yaguven.volume.discount.wizard.line"
    _description = "Venta del mes en el asistente de bonificación"
    _order = "is_current, invoice_date, id"

    wizard_id = fields.Many2one("yaguven.volume.discount.wizard", required=True, ondelete="cascade")
    move_id = fields.Many2one("account.move", string="Comprobante", readonly=True)
    invoice_date = fields.Date(related="move_id.invoice_date", string="Fecha")
    move_type = fields.Selection(related="move_id.move_type", string="Tipo")
    payment_state = fields.Selection(related="move_id.payment_state", string="Cobro")
    currency_id = fields.Many2one(related="wizard_id.currency_id")
    volume = fields.Monetary(string="Suma al mes", readonly=True)
    applied_percent = fields.Float(string="% ya bonificado", digits=(5, 2), readonly=True)
    pending_percent = fields.Float(string="% a bonificar", digits=(5, 2), readonly=True)
    is_current = fields.Boolean(string="Esta factura", readonly=True)
    counts = fields.Boolean(string="Suma", readonly=True)
    note = fields.Char(string="Observación", readonly=True)
