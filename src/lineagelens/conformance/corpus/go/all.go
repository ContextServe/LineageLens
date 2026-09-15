package corpus

import "fmt"

const MaxRetries = 3

type Handler interface {
	Handle(value int) error
}

type Base struct {
	shared int
}

type Order struct {
	Base
	total float64
}

func (o *Order) Handle(value int) error {
	amount := o.total
	repo := Repository{}
	err := repo.Save(amount, "USD")
	return err
}

type Repository struct {
	last float64
}

func (r *Repository) Save(amount float64, currency string) error {
	r.last = amount
	return fmt.Errorf("saved %v", amount)
}

func New(total float64) *Order {
	return &Order{total: total}
}
