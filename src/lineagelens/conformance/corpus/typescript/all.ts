export const MAX_RETRIES = 3;

export interface Handler {
  handle(value: number): boolean;
}

export type Id = string;

export enum Status { Open, Closed }

class Base {
  protected shared: number = 0;
}

export class Order extends Base implements Handler {
  total: number = 0;

  constructor(private items: string[]) {
    super();
  }

  handle(value: number): boolean {
    const amount = this.total + value;
    const repo = new Repository();
    const ok = repo.save(amount, "USD");
    return ok;
  }
}

class Repository {
  save(amount: number, currency: string): boolean {
    this.last = amount;
    return true;
  }
}

export const fetchOrder = async (id: Id) => {
  return fetch(`/api/orders/${id}`);
};
