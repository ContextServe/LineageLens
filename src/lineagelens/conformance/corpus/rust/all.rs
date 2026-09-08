pub const MAX_RETRIES: u32 = 3;

pub trait Handler {
    fn handle(&self, value: u32) -> bool;
}

pub struct Repository {
    pub last: f64,
}

pub struct Order {
    pub total: f64,
}

pub enum Status {
    Open,
    Closed,
}

impl Repository {
    pub fn save(&self, amount: f64, currency: &str) -> bool {
        true
    }
}

impl Handler for Order {
    fn handle(&self, value: u32) -> bool {
        let amount = self.total;
        let repo = Repository { last: 0.0 };
        let ok = repo.save(amount, "USD");
        ok
    }
}
