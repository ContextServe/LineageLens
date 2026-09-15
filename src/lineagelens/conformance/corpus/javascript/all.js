export const MAX_RETRIES = 3;

class Base {
  shared = 0;
}

export class Order extends Base {
  total = 0;

  constructor(items) {
    super();
    this.items = items;
  }

  handle(value) {
    const amount = this.total + value;
    const repo = new Repository();
    const ok = repo.save(amount, "USD");
    return ok;
  }
}

class Repository {
  save(amount, currency) {
    this.last = amount;
    return true;
  }
}

export const fetchOrder = async (id) => fetch("/api/orders");
