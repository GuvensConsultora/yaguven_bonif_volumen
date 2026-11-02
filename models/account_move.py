from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools import date_utils


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    # Toda línea de bonificación (en la factura que cruza el tramo o en la NC por las
    # anteriores) apunta a la factura que bonifica y guarda el % aplicado. De ahí se
    # calcula cuánto lleva bonificado cada factura, sin campos que puedan desfasarse.
    vd_source_move_id = fields.Many2one(
        "account.move", string="Factura bonificada", index="btree_not_null",
        copy=False, readonly=True,
    )
    vd_percent = fields.Float(
        string="% bonificado", digits=(5, 2), copy=False, readonly=True,
    )


class AccountMove(models.Model):
    _inherit = "account.move"

    vd_line_ids = fields.One2many(
        "account.move.line", "vd_source_move_id", string="Bonificaciones aplicadas",
    )

    # ------------------------------------------------------------------ cálculo

    def _vd_product_lines(self):
        """Líneas que forman la base: excluye las de bonificación y los productos que la
        empresa marcó como "no suman" (flete y similares)."""
        self.ensure_one()
        excluded = self.company_id.vd_excluded_product_ids
        return self.invoice_line_ids.filtered(
            lambda l: l.display_type == "product" and not l.vd_source_move_id
            and l.product_id not in excluded
        )

    @staticmethod
    def _vd_gross_factor(line):
        """Factor que lleva el subtotal de la línea a su importe bruto (cantidad x precio,
        antes del descuento de la línea). Con descuento del 100% el subtotal es 0 y no hay
        factor que lo recupere: esa línea no suma."""
        if not line.discount:
            return 1.0
        if line.discount >= 100:
            return 0.0
        return 100.0 / (100.0 - line.discount)

    def _vd_volume(self, base):
        """Aporte del comprobante al acumulado del mes, en moneda de la empresa.

        Positivo para facturas, negativo para NC (el signo sale del balance).
        """
        self.ensure_one()
        total = 0.0
        for line in self._vd_product_lines():
            amount = -line.balance * self._vd_gross_factor(line)
            if base == "total" and line.price_subtotal:
                amount *= line.price_total / line.price_subtotal
            total += amount
        return total

    def _vd_applied_percent(self):
        """% de bonificación que ya lleva esta factura (líneas propias + NC no canceladas)."""
        self.ensure_one()
        lines = self.vd_line_ids.filtered(lambda l: l.parent_state != "cancel")
        return sum(lines.mapped("vd_percent"))

    def _vd_period(self):
        self.ensure_one()
        day = self.invoice_date or fields.Date.context_today(self)
        return date_utils.start_of(day, "month"), date_utils.end_of(day, "month")

    def _vd_month_moves(self):
        """Comprobantes publicados del mes del mismo cliente y empresa, sin incluirse."""
        self.ensure_one()
        date_from, date_to = self._vd_period()
        return self.search([
            ("id", "!=", self.id),
            ("company_id", "=", self.company_id.id),
            ("commercial_partner_id", "=", self.commercial_partner_id.id),
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("state", "=", "posted"),
            ("invoice_date", ">=", date_from),
            ("invoice_date", "<=", date_to),
        ], order="invoice_date, id")

    def _vd_compute(self):
        """Análisis de bonificación para esta factura. None si no aplica.

        Devuelve un dict con la escala, el acumulado, el % alcanzado, el historial del mes
        y lo que falta bonificar (de esta factura y de las anteriores).
        """
        self.ensure_one()
        if self.move_type != "out_invoice":
            return None
        company = self.company_id
        scale = self.env["yaguven.volume.discount.tier"]._get_scale(company, self.partner_id)
        if not scale:
            return None
        if not company.vd_product_id or not company.vd_base:
            raise UserError(self.env._(
                "El cliente %(partner)s tiene escala de bonificación por volumen, pero falta "
                "completar en la empresa %(company)s el producto de bonificación y el monto "
                "que se compara contra la escala.",
                partner=self.commercial_partner_id.display_name, company=company.name,
            ))

        base = company.vd_base
        history = []
        accumulated = 0.0
        for move in self._vd_month_moves():
            counts = move.move_type == "out_refund" or not company.vd_only_paid \
                or move.payment_state in ("paid", "in_payment")
            volume = move._vd_volume(base) if counts else 0.0
            accumulated += volume
            history.append({"move": move, "volume": volume, "counts": counts})

        own_volume = self._vd_volume(base)
        total = accumulated + own_volume
        tier_model = self.env["yaguven.volume.discount.tier"]
        percent = tier_model._percent_for(scale, total)

        pending = []
        if percent:
            candidates = [h["move"] for h in history
                          if h["counts"] and h["move"].move_type == "out_invoice"]
            for move in [self] + candidates:
                missing = percent - move._vd_applied_percent()
                if missing > 0.005:
                    pending.append({"move": move, "percent": missing})

        return {
            "scale": scale,
            "base": base,
            "history": history,
            "accumulated_before": accumulated,
            "own_volume": own_volume,
            "total": total,
            "percent": percent,
            "previous_percent": tier_model._percent_for(scale, accumulated),
            "pending": pending,
        }

    # ------------------------------------------------------- líneas a generar

    def _vd_discount_vals(self, percent):
        """Valores de las líneas de bonificación sobre esta factura: una por grupo de impuestos,
        para que cada alícuota de IVA se ajuste en su proporción. Importe positivo, en la
        moneda de la factura; quien llama le da el signo."""
        self.ensure_one()
        product = self.company_id.vd_product_id
        groups = defaultdict(float)
        for line in self._vd_product_lines():
            included = any(line.tax_ids.mapped("price_include"))
            amount = line.price_total if included else line.price_subtotal
            groups[line.tax_ids] += amount * self._vd_gross_factor(line)
        vals = []
        for taxes, amount in groups.items():
            if not amount:
                continue
            vals.append({
                "product_id": product.id,
                "name": self.env._(
                    "Bonificación por volumen %(pct)s%% s/ %(doc)s",
                    pct=f"{percent:g}", doc=self.name if self.state == "posted" else self.env._("esta factura"),
                ),
                "quantity": 1,
                "price_unit": round(amount * percent / 100, 2),
                "tax_ids": [fields.Command.set(taxes.ids)],
                "vd_source_move_id": self.id,
                "vd_percent": percent,
            })
        return vals

    # ------------------------------------------------------------- confirmación

    def action_post(self):
        if self.env.context.get("vd_checked"):
            return super().action_post()
        triggered = self.filtered(lambda m: (m._vd_compute() or {}).get("pending"))
        if not triggered:
            return super(AccountMove, self.with_context(vd_checked=True)).action_post()
        if len(self) > 1:
            raise UserError(self.env._(
                "Estas facturas generan bonificación por volumen y hay que confirmarlas de a una "
                "para revisar el detalle: %s", ", ".join(triggered.mapped("display_name")),
            ))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Bonificación por volumen"),
            "res_model": "yaguven.volume.discount.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_move_id": self.id},
        }

    def _post(self, soft=True):
        # Red de seguridad para confirmaciones que no pasan por el botón (confirmación
        # masiva, código de otros módulos): no dejar pasar en silencio una bonificación.
        if not self.env.context.get("vd_checked"):
            for move in self.filtered(lambda m: m.move_type == "out_invoice"):
                if (move._vd_compute() or {}).get("pending"):
                    raise UserError(self.env._(
                        "La factura %s genera bonificación por volumen. Confirmala desde el "
                        "botón Confirmar de la factura para revisar el detalle.",
                        move.display_name,
                    ))
        return super()._post(soft=soft)
