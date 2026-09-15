using System;

namespace Corpus {
    public interface IHandler {
        bool Handle(int value);
    }

    public class Base {
        protected int Shared = 0;
    }

    public enum Status { Open, Closed }

    public class Order : Base, IHandler {
        public const int MaxRetries = 3;
        private double total = 0.0;
        public string Name { get; set; }

        public Order(double total) {
            this.total = total;
        }

        public bool Handle(int value) {
            var amount = this.total;
            var repo = new Repository();
            var ok = repo.Save(amount, "USD");
            return ok;
        }

        public void Save(int x) {}
        public void Save(string x) {}
    }

    public class Repository {
        public bool Save(double amount, string currency) {
            return true;
        }
    }
}
