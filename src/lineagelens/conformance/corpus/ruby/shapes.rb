# Every construct the Ruby spec claims, once.

module Billing
  class Invoice
    RATE = 0.2

    def initialize(total)
      @total = total
    end

    def with_tax(rate)
      @total * (1 + rate)
    end

    def self.zero
      new(0)
    end
  end
end
