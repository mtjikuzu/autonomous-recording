public class MethodsDemo {

    public static void greet() {
        System.out.println("Hello! Welcome to the methods tutorial.");
    }

    public static void greetUser(String name) {
        System.out.println("Hello, " + name + "! Nice to meet you.");
    }

    public static int add(int a, int b) {
        return a + b;
    }

    public static double add(double a, double b) {
        return a + b;
    }

    public static int factorial(int n) {
        if (n <= 1) {
            return 1;
        }
        return n * factorial(n - 1);
    }

    public static void main(String[] args) {
        greet();

        greetUser("Alice");
        greetUser("Bob");

        int sum = add(5, 3);
        System.out.println("add(5, 3) = " + sum);

        double dSum = add(2.5, 3.7);
        System.out.println("add(2.5, 3.7) = " + dSum);

        int fact5 = factorial(5);
        System.out.println("factorial(5) = " + fact5);

        int fact7 = factorial(7);
        System.out.println("factorial(7) = " + fact7);
    }
}
