package corpus;

import java.util.List;

/** A conformance corpus. */
public class All extends Base implements Handler {
    public static final String NAME = "all";
    private double total = 0.0;

    public All(String name) {
        this.total = 0.0;
    }

    @Override
    public boolean handle(int value) throws IllegalStateException {
        double amount = this.total;
        Repository repo = new Repository();
        boolean ok = repo.save(amount, NAME);
        return ok;
    }

    public void save(int x) {}
    public void save(String x) {}
    public void save(int x, int y) {}
}

interface Handler {
    boolean handle(int value);
}

class Base {
    protected int shared = 0;
}

enum Status { OPEN, CLOSED }

@interface Marker {
    String value();
}

class Repository {
    boolean save(double amount, String currency) {
        return true;
    }
}
