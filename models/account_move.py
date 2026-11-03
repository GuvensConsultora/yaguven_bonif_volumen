import re
from collections import defaultdict

from markupsafe import Markup

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
    # Compras del mes del cliente, incluida esta factura. En borrador se calcula en vivo
    # (para que quien factura lo vea sin ir al mayor); al confirmar se fija en
    # vd_month_volume y se escribe en las notas, que es lo que se imprime.
    vd_has_scale = fields.Boolean(compute="_compute_vd_month_volume_preview")
    vd_month_volume_preview = fields.Monetary(
        string="Comprado en el mes", currency_field="company_currency_id",
        compute="_compute_vd_month_volume_preview",
        help="Lo facturado al cliente en el mes, neto de notas de crédito, sumando esta factura. "
             "Se compara contra la escala de bonificación por volumen.",
    )
    # Sólo en pantalla y en borrador (pedido 03/11/2026): sale de los mismos tramos de la
    # escala, así que no hay montos aparte que mantener.
    vd_next_tier_text = fields.Char(
        string="Próximo escalón", compute="_compute_vd_month_volume_preview",
        help="Cuánto le falta al cliente en el mes para llegar al siguiente tramo de la escala.",
    )
    vd_month_volume = fields.Monetary(
        string="Comprado en el mes (al confirmar)", currency_field="company_currency_id",
        copy=False, readonly=True,
        help="Lo que el cliente llevaba comprado en el mes cuando se confirmó esta factura.",
    )

    def _compute_vd_month_volume_preview(self):
        for move in self:
            data = move._vd_month_data()
            move.vd_has_scale = bool(data)
            move.vd_month_volume_preview = data["total"] if data else 0.0
            move.vd_next_tier_text = move._vd_next_tier_text(data) if data else False

    def _vd_next_tier_text(self, data):
        nxt = data["scale"].filtered(lambda t: t.amount_from > data["total"])[:1]
        if not nxt:
            return False
        return self.env._(
            "faltan %(amount)s para el %(pct)s%%",
            amount=self.company_currency_id.format(nxt.amount_from - data["total"]),
            pct=f"{nxt.percent:g}",
        )

    def _vd_month_data(self):
        """Como _vd_compute, pero sin error si a la empresa le falta configuración: para
        mostrar el acumulado no hace falta el producto de bonificación."""
        self.ensure_one()
        if self.move_type != "out_invoice" or not self.commercial_partner_id or not self.company_id:
            return None
        if not self.env["yaguven.volume.discount.tier"]._get_scale(self.company_id, self.partner_id):
            return None
        if not self.company_id.vd_base:
            return None
        if not self.company_id.vd_product_id:
            # _vd_compute exige el producto sólo para poder bonificar; el acumulado no lo usa.
            return self.with_context(vd_skip_product_check=True)._vd_compute()
        return self._vd_compute()

    _VD_NOTE_RE = re.compile(r"<p>\s*Compras del mes \d{2}/\d{4}:[^<]*</p>")

    def _vd_stamp_month_volume(self):
        """Al confirmar: fija lo comprado en el mes y lo deja en las notas de la factura
        (una sola línea, reemplaza la de una confirmación anterior)."""
        for move in self:
            data = move._vd_month_data()
            if not data:
                continue
            date_from, _date_to = move._vd_period()
            text = self.env._("Compras del mes %(month)s: %(amount)s",
                              month=date_from.strftime("%m/%Y"),
                              amount=move.company_currency_id.format(data["total"]))
            previous = self._VD_NOTE_RE.sub("", str(move.narration or ""))
            note = Markup("<p>%s</p>") % text
            move.write({
                "vd_month_volume": data["total"],
                "narration": Markup(previous) + note if previous.strip() else note,
            })

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
        if not company.vd_base or (
            not company.vd_product_id and not self.env.context.get("vd_skip_product_check")
        ):
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

    # ------------------------------------------------------- conciliación de la NC

    def _reconcile_reversed_moves(self, reverse_moves, move_reverse_cancel):
        # Al confirmar una NC con comprobante asociado, el nativo la concilia entera contra
        # esa factura. La NC de bonificación cubre varias facturas y se aplica por importe
        # en _vd_reconcile_refund: acá se la deja afuera, sólo cuando la confirma el asistente.
        if not self.env.context.get("vd_refund_reconcile"):
            return super()._reconcile_reversed_moves(reverse_moves, move_reverse_cancel)
        keep = [(m, r) for m, r in zip(self, reverse_moves) if not r.invoice_line_ids.vd_source_move_id]
        if keep:
            super(AccountMove, self.browse([m.id for m, _r in keep]))._reconcile_reversed_moves(
                self.browse([r.id for _m, r in keep]), move_reverse_cancel)
        return reverse_moves

    def _vd_reconcile_refund(self):
        """Aplica la NC de bonificación (self) a cada factura que bonifica, por el importe
        exacto de su línea (con impuestos), y no por lo que el nativo asignaría.

        La conciliación nativa reparte por saldo: la primera factura se llevaría toda la NC.
        Por eso se crean los parciales a mano. Si una factura ya está cobrada (o le queda
        menos saldo que su bonificación), lo que no entra queda como saldo a favor en la NC.
        """
        self.ensure_one()
        recv_types = ("asset_receivable", "liability_payable")
        refund_lines = self.line_ids.filtered(
            lambda l: l.account_id.account_type in recv_types and not l.reconciled
        ).sorted(lambda l: (l.date_maturity or l.date, l.id))
        if not refund_lines:
            return self.env["account.partial.reconcile"]

        # Importe (moneda del comprobante) que le toca a cada factura.
        due = defaultdict(float)
        for line in self.invoice_line_ids.filtered("vd_source_move_id"):
            due[line.vd_source_move_id] += line.price_total
        currency = self.currency_id
        total_lines = sum(due.values())
        # Redondeo: el IVA de la NC se redondea sobre el total y puede no coincidir al
        # centavo con la suma de líneas. La diferencia va a la última factura.
        total_refund = -sum(refund_lines.mapped("amount_residual_currency"))
        sources = sorted(due, key=lambda m: (m.invoice_date, m.id))
        diff = total_refund - total_lines
        if sources and not currency.is_zero(diff) and abs(diff) < 1:
            due[sources[-1]] += diff

        vals_list = []
        refund_iter = iter(refund_lines)
        refund_line = next(refund_iter)
        refund_left = -refund_line.amount_residual_currency
        for source in sources:
            want = currency.round(due[source])
            inv_lines = source.line_ids.filtered(
                lambda l: l.account_id in refund_lines.account_id and not l.reconciled
                and l.amount_residual_currency > 0
            ).sorted(lambda l: (l.date_maturity or l.date, l.id))
            for inv_line in inv_lines:
                while refund_line and currency.is_zero(refund_left):
                    refund_line = next(refund_iter, None)
                    refund_left = refund_line and -refund_line.amount_residual_currency or 0.0
                if not refund_line or currency.is_zero(want):
                    break
                amount_cur = currency.round(min(want, inv_line.amount_residual_currency, refund_left))
                if currency.is_zero(amount_cur):
                    continue
                # Moneda de la empresa: a la tasa de cada línea, sin pasarse de su saldo.
                debit_amt = min(inv_line.amount_residual,
                                inv_line.amount_residual * amount_cur / inv_line.amount_residual_currency)
                credit_amt = min(-refund_line.amount_residual,
                                 -refund_line.amount_residual * amount_cur / -refund_line.amount_residual_currency)
                vals_list.append({
                    "debit_move_id": inv_line.id,
                    "credit_move_id": refund_line.id,
                    "amount": self.company_currency_id.round(min(debit_amt, credit_amt)),
                    "debit_amount_currency": amount_cur,
                    "credit_amount_currency": amount_cur,
                })
                want -= amount_cur
                refund_left -= amount_cur
        return self.env["account.partial.reconcile"].create(vals_list)

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
        self.filtered(lambda m: m.move_type == "out_invoice")._vd_stamp_month_volume()
        return super()._post(soft=soft)
