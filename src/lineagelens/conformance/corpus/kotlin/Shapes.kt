// Every construct the Kotlin spec claims, once.

package billing

class Invoice(val total: Int) {
    val rate: Int = 20

    fun withTax(pct: Int): Int = total * (100 + pct) / 100
}

interface Ledger {
    fun record(amount: Int)
}

object Registry {
    fun lookup(name: String): Int = 0
}

enum class Currency {
    GBP,
    USD
}
